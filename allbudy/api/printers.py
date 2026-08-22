"""API du parc d'imprimantes: configuration, etat temps reel et commandes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..events import record_event
from ..files.store import local_path
from ..models import ACTIVE_JOB_STATUSES, GcodeFile, Job, JobStatus, Printer, TransportKind
from ..printers.base import PrinterError
from ..printers.discovery import default_network, identify_model, scan
from ..printers.manager import DEFAULT_PORTS, manager, sync_virtual_printers
from ..queueing import scheduler
from ..schemas import (
    DiscoveryRequest,
    ExtrudeCommand,
    FanCommand,
    GcodeCommand,
    HomeCommand,
    LightCommand,
    MessageResponse,
    MoveCommand,
    PrintCommand,
    PrinterCreate,
    PrinterOut,
    PrinterUpdate,
    SpeedCommand,
    TemperatureCommand,
)
from ..security import current_user

router = APIRouter(prefix="/api/printers", tags=["printers"], dependencies=[Depends(current_user)])


async def _get_printer(session: AsyncSession, printer_id: int) -> Printer:
    printer = await session.get(Printer, printer_id)
    if printer is None:
        raise HTTPException(status_code=404, detail="Imprimante introuvable")
    return printer


async def _sync_and_register_virtuals(session: AsyncSession) -> None:
    """Un nouveau modele reel (creation ou changement via update) doit avoir
    sa virtuelle sans action manuelle."""
    created = await sync_virtual_printers(session)
    if not created:
        return
    await session.commit()
    for virtual in created:
        await manager.add(virtual)
        await record_event(
            f"Imprimante virtuelle ajoutee: {virtual.name}",
            category="printer", printer_id=virtual.id,
        )


def _wrap(action):
    """Traduit les erreurs transport en reponses HTTP exploitables."""

    async def run(printer_id: int):
        try:
            return await manager.command(printer_id, action)
        except PrinterError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return run


# ------------------------------------------------------------------ CRUD
@router.get("", response_model=list[PrinterOut])
async def list_printers(session: AsyncSession = Depends(get_session)) -> list[Printer]:
    result = await session.execute(select(Printer).order_by(Printer.sort_order, Printer.id))
    return list(result.scalars())


@router.post("", response_model=PrinterOut, status_code=201)
async def create_printer(
    payload: PrinterCreate, session: AsyncSession = Depends(get_session)
) -> Printer:
    data = payload.model_dump()
    data["transport"] = payload.transport.value
    data["port"] = payload.port or DEFAULT_PORTS.get(data["transport"], 7125)
    printer = Printer(**data)
    session.add(printer)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Ce nom d'imprimante existe deja") from exc
    await session.refresh(printer)
    await manager.add(printer)
    await record_event(f"Imprimante ajoutee: {printer.name}", category="printer",
                       printer_id=printer.id)
    await _sync_and_register_virtuals(session)
    return printer


@router.get("/discover")
async def discover(
    network: str | None = None, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Balaie le reseau local a la recherche d'imprimantes."""
    try:
        candidates = await scan(network)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    known = {
        (host, port)
        for host, port in (await session.execute(select(Printer.host, Printer.port))).all()
    }
    return {
        "network": network or default_network(),
        "results": [
            {**candidate.to_dict(), "known": (candidate.host, candidate.port) in known}
            for candidate in candidates
        ],
    }


@router.post("/discover")
async def discover_post(
    payload: DiscoveryRequest, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    try:
        candidates = await scan(payload.network, payload.ports)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    known = {
        (host, port)
        for host, port in (await session.execute(select(Printer.host, Printer.port))).all()
    }
    return {
        "network": payload.network or default_network(),
        "results": [
            {**candidate.to_dict(), "known": (candidate.host, candidate.port) in known}
            for candidate in candidates
        ],
    }


@router.post("/{printer_id}/detect-model")
async def detect_model(
    printer_id: int, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Interroge la machine pour retrouver son vrai modele commercial.

    Fiable en LAN Creality (le protocole le rapporte); best-effort en
    Moonraker (heuristique sur le nom d'hote). Renvoie `model: null` plutot
    que de deviner quand rien de fiable n'est trouve.
    """
    printer = await _get_printer(session, printer_id)
    model = await identify_model(printer.host, printer.port, printer.transport)
    return {"model": model}


@router.get("/status")
async def all_status() -> list[dict[str, Any]]:
    """Instantane de tout le parc (le WebSocket pousse les mises a jour)."""
    return manager.snapshot()


@router.get("/{printer_id}", response_model=PrinterOut)
async def get_printer(printer_id: int, session: AsyncSession = Depends(get_session)) -> Printer:
    return await _get_printer(session, printer_id)


@router.patch("/{printer_id}", response_model=PrinterOut)
async def update_printer(
    printer_id: int, payload: PrinterUpdate, session: AsyncSession = Depends(get_session)
) -> Printer:
    printer = await _get_printer(session, printer_id)
    data = payload.model_dump(exclude_unset=True)
    if isinstance(data.get("transport"), TransportKind):
        data["transport"] = data["transport"].value
        data.setdefault("port", DEFAULT_PORTS.get(data["transport"], printer.port))
    for key, value in data.items():
        setattr(printer, key, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Ce nom d'imprimante existe deja") from exc
    await session.refresh(printer)
    await manager.reload(printer)
    await _sync_and_register_virtuals(session)
    return printer


@router.delete("/{printer_id}", response_model=MessageResponse)
async def delete_printer(
    printer_id: int, session: AsyncSession = Depends(get_session)
) -> MessageResponse:
    printer = await _get_printer(session, printer_id)
    active = (
        await session.execute(
            select(Job.id).where(
                Job.printer_id == printer_id,
                Job.status.in_([s.value for s in ACTIVE_JOB_STATUSES]),
            )
        )
    ).first()
    if active:
        raise HTTPException(
            status_code=409, detail="Un travail est en cours sur cette imprimante"
        )
    name = printer.name
    await manager.remove(printer_id)
    await session.delete(printer)
    await session.commit()
    await record_event(f"Imprimante supprimee: {name}", category="printer")
    return MessageResponse(message=f"Imprimante {name} supprimee")


# ------------------------------------------------------------------ etat
@router.get("/{printer_id}/status")
async def printer_status(printer_id: int, session: AsyncSession = Depends(get_session)):
    await _get_printer(session, printer_id)
    runtime = manager.get(printer_id)
    if runtime is None:
        raise HTTPException(status_code=503, detail="Imprimante non demarree")
    return runtime.to_dict()


@router.post("/{printer_id}/reconnect", response_model=MessageResponse)
async def reconnect(
    printer_id: int, session: AsyncSession = Depends(get_session)
) -> MessageResponse:
    printer = await _get_printer(session, printer_id)
    await manager.reload(printer)
    return MessageResponse(message=f"Reconnexion de {printer.name} demandee")


async def _require_virtual(session: AsyncSession, printer_id: int) -> Printer:
    printer = await _get_printer(session, printer_id)
    if printer.transport != TransportKind.VIRTUAL.value:
        raise HTTPException(
            status_code=400, detail="Cette action n'existe que pour une imprimante virtuelle"
        )
    return printer


@router.post("/{printer_id}/virtual/start", response_model=MessageResponse)
async def confirm_virtual_start(
    printer_id: int, session: AsyncSession = Depends(get_session)
) -> MessageResponse:
    """L'operateur a physiquement lance l'impression a partir du fichier envoye sur le NAS."""
    printer = await _require_virtual(session, printer_id)
    await _wrap(lambda t: t.confirm_start())(printer_id)
    await record_event(
        f"Demarrage confirme sur {printer.name}", category="printer", printer_id=printer_id
    )
    scheduler.wake()
    return MessageResponse(message="Impression marquee en cours")


@router.post("/{printer_id}/virtual/finish", response_model=MessageResponse)
async def confirm_virtual_finish(
    printer_id: int, session: AsyncSession = Depends(get_session)
) -> MessageResponse:
    """L'operateur confirme que l'impression est terminee (pas de telemetrie a attendre)."""
    printer = await _require_virtual(session, printer_id)
    await _wrap(lambda t: t.confirm_finished())(printer_id)
    await record_event(
        f"Fin confirmee sur {printer.name}", category="printer", printer_id=printer_id
    )
    scheduler.wake()
    return MessageResponse(message="Impression marquee terminee")


# -------------------------------------------------------------- commandes
@router.post("/{printer_id}/pause", response_model=MessageResponse)
async def pause(printer_id: int) -> MessageResponse:
    await _wrap(lambda t: t.pause())(printer_id)
    await record_event("Impression mise en pause", category="printer", printer_id=printer_id)
    return MessageResponse(message="Pause demandee")


@router.post("/{printer_id}/resume", response_model=MessageResponse)
async def resume(printer_id: int) -> MessageResponse:
    await _wrap(lambda t: t.resume())(printer_id)
    await record_event("Impression reprise", category="printer", printer_id=printer_id)
    return MessageResponse(message="Reprise demandee")


@router.post("/{printer_id}/cancel", response_model=MessageResponse)
async def cancel(printer_id: int) -> MessageResponse:
    await _wrap(lambda t: t.cancel())(printer_id)
    await record_event(
        "Impression annulee depuis AllBudy", level="warning", category="printer",
        printer_id=printer_id,
    )
    return MessageResponse(message="Annulation demandee")


@router.post("/{printer_id}/emergency-stop", response_model=MessageResponse)
async def emergency_stop(printer_id: int) -> MessageResponse:
    await _wrap(lambda t: t.emergency_stop())(printer_id)
    await record_event(
        "ARRET D'URGENCE declenche", level="error", category="printer", printer_id=printer_id
    )
    return MessageResponse(message="Arret d'urgence envoye")


@router.post("/{printer_id}/temperature", response_model=MessageResponse)
async def set_temperature(printer_id: int, payload: TemperatureCommand) -> MessageResponse:
    await _wrap(lambda t: t.set_temperature(payload.heater, payload.value))(printer_id)
    return MessageResponse(message=f"Consigne {payload.heater} a {payload.value:.0f} C")


@router.post("/{printer_id}/fan", response_model=MessageResponse)
async def set_fan(printer_id: int, payload: FanCommand) -> MessageResponse:
    await _wrap(lambda t: t.set_fan(payload.fan, payload.speed))(printer_id)
    return MessageResponse(message=f"Ventilateur {payload.fan} a {payload.speed:.0f} %")


@router.post("/{printer_id}/light", response_model=MessageResponse)
async def set_light(printer_id: int, payload: LightCommand) -> MessageResponse:
    await _wrap(lambda t: t.set_light(payload.on))(printer_id)
    return MessageResponse(message="Eclairage " + ("allume" if payload.on else "eteint"))


@router.post("/{printer_id}/home", response_model=MessageResponse)
async def home(printer_id: int, payload: HomeCommand) -> MessageResponse:
    await _wrap(lambda t: t.home(payload.axes))(printer_id)
    return MessageResponse(message=f"Prise d'origine {payload.axes}")


@router.post("/{printer_id}/move", response_model=MessageResponse)
async def move(printer_id: int, payload: MoveCommand) -> MessageResponse:
    await _wrap(lambda t: t.move(payload.axis.upper(), payload.distance, payload.speed))(printer_id)
    return MessageResponse(message=f"Deplacement {payload.axis.upper()} {payload.distance:+g} mm")


@router.post("/{printer_id}/extrude", response_model=MessageResponse)
async def extrude(printer_id: int, payload: ExtrudeCommand) -> MessageResponse:
    await _wrap(lambda t: t.extrude(payload.distance, payload.speed))(printer_id)
    return MessageResponse(message=f"Extrusion {payload.distance:+g} mm")


@router.post("/{printer_id}/speed", response_model=MessageResponse)
async def speed(printer_id: int, payload: SpeedCommand) -> MessageResponse:
    await _wrap(lambda t: t.set_speed_factor(payload.percent))(printer_id)
    return MessageResponse(message=f"Vitesse a {payload.percent:.0f} %")


@router.post("/{printer_id}/gcode", response_model=MessageResponse)
async def send_gcode(printer_id: int, payload: GcodeCommand) -> MessageResponse:
    await _wrap(lambda t: t.send_gcode(payload.script))(printer_id)
    await record_event(
        f"G-code envoye: {payload.script.splitlines()[0][:80]}",
        category="printer",
        printer_id=printer_id,
    )
    return MessageResponse(message="G-code envoye")


# --------------------------------------------------------------- fichiers
@router.get("/{printer_id}/files")
async def printer_files(printer_id: int) -> list[dict[str, Any]]:
    files = await _wrap(lambda t: t.list_files())(printer_id)
    return [
        {"name": f.name, "size": f.size, "modified": f.modified, "path": f.path} for f in files
    ]


@router.post("/{printer_id}/print", response_model=MessageResponse)
async def print_file(
    printer_id: int, payload: PrintCommand, session: AsyncSession = Depends(get_session)
) -> MessageResponse:
    """Envoie un fichier de la bibliotheque et lance l'impression immediatement.

    Court-circuite la file d'attente: utile pour un envoi manuel cible.
    """
    printer = await _get_printer(session, printer_id)
    record = await session.get(GcodeFile, payload.file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Fichier introuvable")
    if not (record.meta or {}).get("printable", record.kind == "gcode"):
        raise HTTPException(
            status_code=400,
            detail="Ce 3MF n'est pas tranche: il ne peut pas etre imprime tel quel",
        )
    path = local_path(record.stored_name)
    if not path.exists():
        raise HTTPException(status_code=410, detail="Fichier absent du disque")

    if not manager.is_free(printer_id):
        raise HTTPException(status_code=409, detail=f"{printer.name} n'est pas disponible")

    remote_name = record.stored_name
    await _wrap(lambda t: t.upload(path, remote_name, start=False))(printer_id)
    if payload.start:
        await _wrap(lambda t: t.start_print(remote_name))(printer_id)

    job = Job(
        name=record.filename,
        file_id=record.id,
        printer_id=printer_id,
        status=JobStatus.PRINTING.value if payload.start else JobStatus.COMPLETED.value,
        remote_filename=remote_name,
        auto_start=False,
        copies=1,
        copies_done=0 if payload.start else 1,
    )
    session.add(job)
    await session.commit()
    await record_event(
        f"Envoi manuel de '{record.filename}' vers {printer.name}",
        category="job",
        printer_id=printer_id,
        job_id=job.id,
    )
    scheduler.wake()
    return MessageResponse(
        message=f"'{record.filename}' envoye vers {printer.name}", data={"job_id": job.id}
    )


@router.delete("/{printer_id}/files/{name}", response_model=MessageResponse)
async def delete_printer_file(printer_id: int, name: str) -> MessageResponse:
    await _wrap(lambda t: t.delete_file(name))(printer_id)
    return MessageResponse(message=f"{name} supprime de l'imprimante")
