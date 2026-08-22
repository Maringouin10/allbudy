"""Dispatcher de la file d'attente.

Une boucle unique fait deux choses a intervalle regulier:

1. suivre les travaux en cours (progression, fin, echec) depuis l'etat remonte
   par les imprimantes;
2. attribuer les travaux en attente aux machines libres et compatibles, puis
   televerser et lancer l'impression.

Un seul dispatcher tourne par instance, et l'envoi d'un travail est protege par
un verrou par imprimante: deux travaux ne peuvent pas partir sur la meme
machine.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from ..config import get_settings
from ..db import session_scope
from ..events import bus, record_event
from ..files.remotes import RemoteError, upload_remote
from ..files.store import local_path
from ..models import (
    ACTIVE_JOB_STATUSES,
    GcodeFile,
    Job,
    JobStatus,
    Printer,
    RemoteStorage,
    Spool,
    TransportKind,
)
from ..printers.base import (
    STATE_COMPLETE,
    STATE_ERROR,
    STATE_IDLE,
    STATE_PAUSED,
    STATE_PRINTING,
    PrinterError,
)
from ..printers.manager import PrinterManager
from ..webhooks import notify_print_finished
from .matcher import evaluate

log = logging.getLogger("allbudy.scheduler")

#: Delai laisse a la machine pour signaler qu'elle imprime apres un lancement.
START_GRACE_SECONDS = 90.0
MAX_ATTEMPTS = 3

#: Macro Klipper standard pour recalibrer le maillage du plateau avant impression.
BED_MESH_MACRO = "BED_MESH_CALIBRATE"


def _seconds_since(moment: datetime | None) -> float:
    """Secondes ecoulees depuis `moment`; +infini s'il est inconnu.

    SQLite ne stocke pas de fuseau: une date relue peut revenir naive, on la
    considere alors comme de l'UTC (c'est ce qu'on ecrit).
    """
    if moment is None:
        return float("inf")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (datetime.now(UTC) - moment).total_seconds()


class JobScheduler:
    def __init__(self, manager: PrinterManager) -> None:
        self.manager = manager
        self.settings = get_settings()
        self.enabled = True
        self._task: asyncio.Task[None] | None = None
        self._printer_locks: dict[int, asyncio.Lock] = {}
        self._wake = asyncio.Event()

    # ------------------------------------------------------------ cycle de vie
    async def start(self) -> None:
        if self._task is not None:
            return
        # Les verrous appartiennent a une boucle d'evenements donnee: en
        # reutiliser un cree par une boucle precedente leve a l'acquisition.
        self._printer_locks.clear()
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="job-scheduler")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        self._printer_locks.clear()

    def wake(self) -> None:
        """Declenche un tour immediat (apres ajout d'un travail par exemple)."""
        self._wake.set()

    def _lock(self, printer_id: int) -> asyncio.Lock:
        return self._printer_locks.setdefault(printer_id, asyncio.Lock())

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - la boucle ne doit jamais mourir
                log.exception("Erreur dans le dispatcher")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.settings.scheduler_interval)
            except TimeoutError:
                pass
            self._wake.clear()

    async def tick(self) -> None:
        await self.track_running_jobs()
        if self.enabled:
            await self.dispatch()

    # ------------------------------------------------------- suivi des travaux
    async def track_running_jobs(self) -> None:
        """Met a jour les travaux actifs a partir de l'etat des machines."""
        async with session_scope() as session:
            jobs = (
                (
                    await session.execute(
                        select(Job).where(
                            Job.status.in_([s.value for s in ACTIVE_JOB_STATUSES]),
                            Job.printer_id.is_not(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for job in jobs:
                await self._track_job(session, job)

    async def _track_job(self, session, job: Job) -> None:
        runtime = self.manager.get(job.printer_id) if job.printer_id else None
        if runtime is None or not runtime.connected:
            return  # Machine injoignable: on attend son retour sans rien conclure.
        if job.status == JobStatus.SENDING.value:
            return  # Le televersement en cours pilote lui-meme la transition.

        status = runtime.status
        # Une machine met quelques secondes a passer de "libre" a "imprime"
        # apres un lancement: sans ce delai, on conclurait a un echec.
        in_grace = _seconds_since(job.started_at or job.assigned_at) < START_GRACE_SECONDS

        if status.state == STATE_PRINTING:
            if job.status != JobStatus.PRINTING.value:
                job.status = JobStatus.PRINTING.value
                job.started_at = job.started_at or datetime.now(UTC)
            job.progress = status.progress
        elif status.state == STATE_PAUSED:
            job.status = JobStatus.PAUSED.value
            job.progress = status.progress
        elif status.state == STATE_COMPLETE:
            await self._complete_job(session, job, success=True)
        elif status.state == STATE_ERROR:
            await self._complete_job(
                session, job, success=False, error=status.state_message or "erreur machine"
            )
        elif status.state == STATE_IDLE and not in_grace:
            if job.status in (JobStatus.PRINTING.value, JobStatus.PAUSED.value):
                # Retour a l'arret sans passer par "complete": arret manuel.
                if job.progress >= 99.0:
                    await self._complete_job(session, job, success=True)
                else:
                    await self._complete_job(
                        session, job, success=False, error="impression interrompue sur la machine"
                    )
            elif job.status == JobStatus.ASSIGNED.value:
                await self._complete_job(
                    session, job, success=False, error="l'impression n'a jamais demarre"
                )

    async def _complete_job(
        self, session, job: Job, *, success: bool, error: str | None = None
    ) -> None:
        printer_id = job.printer_id
        if success:
            job.copies_done += 1
            job.progress = 100.0
            if job.copies_done < job.copies:
                # Exemplaire suivant: le travail retourne en file d'attente.
                job.status = JobStatus.QUEUED.value
                job.printer_id = None
                job.progress = 0.0
                job.assigned_at = None
                job.started_at = None
                await record_event(
                    f"Travail #{job.id} '{job.name}': exemplaire "
                    f"{job.copies_done}/{job.copies} termine, remis en file",
                    category="job",
                    printer_id=printer_id,
                    job_id=job.id,
                    session=session,
                )
            else:
                job.status = JobStatus.COMPLETED.value
                job.finished_at = datetime.now(UTC)
                await record_event(
                    f"Travail #{job.id} '{job.name}' termine",
                    category="job",
                    printer_id=printer_id,
                    job_id=job.id,
                    session=session,
                )
                if printer_id is not None:
                    printer = await session.get(Printer, printer_id)
                    if printer is not None:
                        # Tant que ce n'est pas confirme, la file ne renvoie rien
                        # sur cette machine: on eviterait sinon d'imprimer par-
                        # dessus une piece pas encore retiree du plateau.
                        printer.bed_cleared = False
                    # Le plateau est encore chaud: on ne guette sa temperature
                    # qu'a partir de maintenant (voir PrinterManager._check_bed_cold).
                    self.manager.arm_bed_cold(printer_id)
                    # Tache independante: l'appel HTTP d'un webhook ne doit pas
                    # retenir la transaction en cours (voir _send_job plus bas
                    # pour le meme principe applique a l'envoi du fichier).
                    asyncio.create_task(self._notify_print_finished(printer_id, job.id))
        else:
            job.attempts += 1
            job.error = error
            if job.attempts < MAX_ATTEMPTS and job.auto_start:
                job.status = JobStatus.QUEUED.value
                job.printer_id = None
                job.progress = 0.0
                job.assigned_at = None
                job.started_at = None
                await record_event(
                    f"Travail #{job.id} '{job.name}' echoue ({error}) - nouvelle tentative "
                    f"{job.attempts}/{MAX_ATTEMPTS}",
                    level="warning",
                    category="job",
                    printer_id=printer_id,
                    job_id=job.id,
                    session=session,
                )
            else:
                job.status = JobStatus.FAILED.value
                job.finished_at = datetime.now(UTC)
                await record_event(
                    f"Travail #{job.id} '{job.name}' en echec: {error}",
                    level="error",
                    category="job",
                    printer_id=printer_id,
                    job_id=job.id,
                    session=session,
                )
                if printer_id is not None:
                    # Meme logique que sur une reussite: la piece (ratee) peut
                    # encore etre sur le plateau, on ne renvoie rien dessus
                    # avant confirmation manuelle.
                    printer = await session.get(Printer, printer_id)
                    if printer is not None:
                        printer.bed_cleared = False
        bus.publish("job.updated", {"job_id": job.id, "status": job.status})

    async def _notify_print_finished(self, printer_id: int, job_id: int) -> None:
        """Tache independante: sa propre transaction, hors du chemin critique."""
        async with session_scope() as session:
            printer = await session.get(Printer, printer_id)
            job = await session.get(Job, job_id)
            if printer is None or job is None:
                return
            await notify_print_finished(session, printer, job)

    # -------------------------------------------------------------- dispatch
    async def dispatch(self) -> None:
        async with session_scope() as session:
            queued = (
                (
                    await session.execute(
                        select(Job)
                        .options(selectinload(Job.file))
                        .where(
                            Job.status == JobStatus.QUEUED.value,
                            Job.auto_start.is_(True),
                            or_(Job.scheduled_at.is_(None), Job.scheduled_at <= datetime.now(UTC)),
                        )
                        .order_by(Job.priority.desc(), Job.position.asc(), Job.id.asc())
                    )
                )
                .scalars()
                .all()
            )
            if not queued:
                return

            printers = (await session.execute(select(Printer))).scalars().all()
            spools_by_printer: dict[int, list[Spool]] = {}
            for spool in (await session.execute(select(Spool))).scalars():
                if spool.printer_id is not None:
                    spools_by_printer.setdefault(spool.printer_id, []).append(spool)

            # Une machine ne prend qu'un travail par tour.
            claimed: set[int] = set()
            assigned: list[int] = []
            for job in queued:
                candidate = self._best_printer(job, printers, spools_by_printer, claimed)
                if candidate is None:
                    continue
                claimed.add(candidate.id)
                job.status = JobStatus.ASSIGNED.value
                job.printer_id = candidate.id
                job.assigned_at = datetime.now(UTC)
                job.error = None
                await session.flush()
                await record_event(
                    f"Travail #{job.id} '{job.name}' attribue a {candidate.name}",
                    category="job",
                    printer_id=candidate.id,
                    job_id=job.id,
                    session=session,
                )
                assigned.append(job.id)

        # L'envoi ne demarre qu'apres la fermeture de la transaction ci-dessus:
        # lance plus tot, il relirait l'ancien statut du travail.
        for job_id in assigned:
            asyncio.create_task(self._send_job(job_id))

    def _best_printer(
        self,
        job: Job,
        printers: list[Printer],
        spools_by_printer: dict[int, list[Spool]],
        claimed: set[int],
    ) -> Printer | None:
        best: tuple[float, Printer] | None = None
        for printer in printers:
            if printer.id in claimed:
                continue
            runtime = self.manager.get(printer.id)
            result = evaluate(
                job,
                job.file,
                printer,
                spools_by_printer.get(printer.id, []),
                connected=bool(runtime and runtime.connected),
                free=self.manager.is_free(printer.id),
            )
            if not result.ok:
                continue
            if best is None or result.score > best[0]:
                best = (result.score, printer)
        return best[1] if best else None

    async def _send_job(self, job_id: int) -> None:
        """Televerse le fichier puis lance l'impression.

        Pour une imprimante virtuelle, « televerser » veut dire envoyer le
        fichier sur son depot NAS configure plutot que sur une machine, et
        « lancer » n'arme qu'une attente de confirmation manuelle: il n'y a
        pas de telemetrie pour savoir si l'impression a reellement demarre.
        """
        async with session_scope() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status != JobStatus.ASSIGNED.value or job.printer_id is None:
                return
            file = await session.get(GcodeFile, job.file_id)
            printer_id = job.printer_id
            if file is None:
                await self._complete_job(session, job, success=False, error="fichier introuvable")
                return
            path = local_path(file.stored_name)
            if not path.exists():
                await self._complete_job(
                    session, job, success=False, error="fichier absent du disque"
                )
                return
            printer = await session.get(Printer, printer_id)
            is_virtual = bool(printer and printer.transport == TransportKind.VIRTUAL.value)
            virtual_storage = None
            if is_virtual:
                storage_id = printer.virtual_target_storage_id
                if storage_id is None:
                    await self._complete_job(
                        session, job, success=False,
                        error="aucun depot NAS configure pour cette imprimante virtuelle",
                    )
                    return
                virtual_storage = await session.get(RemoteStorage, storage_id)
                if virtual_storage is None:
                    await self._complete_job(
                        session, job, success=False, error="depot NAS configure introuvable"
                    )
                    return
            remote_name = file.stored_name
            bed_leveling = job.bed_leveling
            job.status = JobStatus.SENDING.value
            job.remote_filename = remote_name
        bus.publish("job.updated", {"job_id": job_id, "status": JobStatus.SENDING.value})

        error: str | None = None
        try:
            # Le verrou couvre televersement + lancement: deux travaux ne
            # peuvent pas se marcher dessus sur la meme machine.
            async with self._lock(printer_id):
                if is_virtual:
                    target = f"{(virtual_storage.remote_path or '/').rstrip('/')}/{remote_name}"
                    await upload_remote(virtual_storage, path, target)
                else:
                    await self.manager.command(
                        printer_id, lambda t: t.upload(path, remote_name, start=False)
                    )
                    if bed_leveling:
                        try:
                            await self.manager.command(
                                printer_id, lambda t: t.send_gcode(BED_MESH_MACRO)
                            )
                        except PrinterError as exc:
                            # Une machine sans cette macro ne doit pas bloquer l'impression.
                            log.warning(
                                "Nivellement du plateau echoue pour le travail %s: %s", job_id, exc
                            )
                await self.manager.command(printer_id, lambda t: t.start_print(remote_name))
        except (PrinterError, RemoteError) as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 - sinon le travail resterait en "envoi"
            log.exception("Echec inattendu de l'envoi du travail %s", job_id)
            error = f"erreur inattendue: {exc}"

        async with session_scope() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return
            if error:
                await self._complete_job(session, job, success=False, error=error)
                return
            if is_virtual:
                # Retour a "assigned": pas de vraie impression en cours, en
                # attente de confirmation manuelle (voir l'action « demarrer »
                # sur l'imprimante virtuelle).
                job.status = JobStatus.ASSIGNED.value
                await record_event(
                    f"'{job.name}' envoye sur le depot NAS, en attente de demarrage manuel",
                    category="job", printer_id=printer_id, job_id=job_id, session=session,
                )
                bus.publish("job.updated", {"job_id": job_id, "status": job.status})
                return
            job.status = JobStatus.PRINTING.value
            job.started_at = datetime.now(UTC)
            await record_event(
                f"Impression lancee: '{job.name}' sur imprimante #{printer_id}",
                category="job",
                printer_id=printer_id,
                job_id=job_id,
                session=session,
            )
        bus.publish("job.updated", {"job_id": job_id, "status": JobStatus.PRINTING.value})

    # ------------------------------------------------------------- diagnostic
    async def explain(self, job_id: int) -> list[dict[str, Any]]:
        """Detaille, imprimante par imprimante, pourquoi un travail n'est pas parti."""
        async with session_scope() as session:
            job = await session.get(Job, job_id, options=[selectinload(Job.file)])
            if job is None:
                return []
            printers = (await session.execute(select(Printer))).scalars().all()
            spools_by_printer: dict[int, list[Spool]] = {}
            for spool in (await session.execute(select(Spool))).scalars():
                if spool.printer_id is not None:
                    spools_by_printer.setdefault(spool.printer_id, []).append(spool)

            report = []
            for printer in printers:
                runtime = self.manager.get(printer.id)
                result = evaluate(
                    job,
                    job.file,
                    printer,
                    spools_by_printer.get(printer.id, []),
                    connected=bool(runtime and runtime.connected),
                    free=self.manager.is_free(printer.id),
                )
                report.append(
                    {"printer_id": printer.id, "printer": printer.name, **result.to_dict()}
                )
            return report
