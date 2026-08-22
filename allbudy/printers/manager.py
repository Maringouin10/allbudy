"""Gestionnaire du parc: connexions, reconnexion, cache d'etat, synchro CFS."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import session_scope
from ..events import bus, record_event
from ..models import Printer, Spool, TransportKind, Webhook, WebhookEvent
from ..webhooks import bed_cold_payload, fire, matching_webhooks
from .base import (
    STATE_OFFLINE,
    PrinterConfig,
    PrinterError,
    PrinterStatus,
    PrinterTransport,
    SpoolState,
)
from .creality_lan import CrealityLanTransport
from .moonraker import MoonrakerTransport
from .simulator import SimulatorTransport
from .virtual import VirtualTransport

log = logging.getLogger("allbudy.manager")

T = TypeVar("T")

TRANSPORTS: dict[str, type[PrinterTransport]] = {
    TransportKind.MOONRAKER.value: MoonrakerTransport,
    TransportKind.CREALITY_LAN.value: CrealityLanTransport,
    TransportKind.SIMULATOR.value: SimulatorTransport,
    TransportKind.VIRTUAL.value: VirtualTransport,
}

#: Ports par defaut par protocole.
DEFAULT_PORTS = {
    TransportKind.MOONRAKER.value: 7125,
    TransportKind.CREALITY_LAN.value: 9999,
    TransportKind.SIMULATOR.value: 0,
    TransportKind.VIRTUAL.value: 0,
}


#: Suffixe distinguant une imprimante virtuelle dans la liste.
VIRTUAL_NAME_SUFFIX = " (virtuelle)"


async def sync_virtual_printers(session: AsyncSession) -> list[Printer]:
    """Garantit une imprimante virtuelle par modele reel distinct du parc.

    Ne supprime jamais une virtuelle existante, meme si son modele n'a plus
    de machine reelle correspondante: sa cible NAS deja configuree serait
    perdue pour rien. Seule la creation est automatique.
    """
    rows = (await session.execute(select(Printer))).scalars().all()
    real_models = {
        p.model for p in rows if p.transport != TransportKind.VIRTUAL.value and p.model
    }
    virtual_models = {p.model for p in rows if p.transport == TransportKind.VIRTUAL.value}
    existing_names = {p.name for p in rows}

    created = []
    for model in sorted(real_models - virtual_models):
        name = f"{model}{VIRTUAL_NAME_SUFFIX}"
        if name in existing_names:
            continue
        printer = Printer(
            name=name,
            model=model,
            transport=TransportKind.VIRTUAL.value,
            host="virtual",
            port=0,
            enabled=True,
            auto_assign=True,
        )
        session.add(printer)
        existing_names.add(name)
        created.append(printer)
    if created:
        await session.flush()
    return created


def build_config(printer: Printer) -> PrinterConfig:
    return PrinterConfig(
        id=printer.id,
        name=printer.name,
        host=printer.host,
        port=printer.port or DEFAULT_PORTS.get(printer.transport, 7125),
        model=printer.model,
        api_key=printer.api_key,
        upload_root=printer.upload_root or "gcodes",
        has_cfs=printer.has_cfs,
        nozzle_diameter=printer.nozzle_diameter,
    )


class PrinterRuntime:
    """Etat vivant d'une imprimante: sa connexion et son dernier statut."""

    def __init__(self, printer: Printer) -> None:
        self.id = printer.id
        self.name = printer.name
        self.enabled = printer.enabled
        self.auto_assign = printer.auto_assign
        self.transport_kind = printer.transport
        self.config = build_config(printer)
        self.transport: PrinterTransport | None = None
        self.status = PrinterStatus(state=STATE_OFFLINE)
        self.last_error: str | None = None
        self.connected = False
        self.task: asyncio.Task[None] | None = None
        self._cfs_fingerprint: str | None = None
        self._lock = asyncio.Lock()
        self.bed_cold_armed = False
        """Un travail vient de se terminer: on guette le refroidissement du plateau."""
        self.bed_cold_notified: set[int] = set()
        """Ids des webhooks bed_cold deja notifies depuis le dernier armement."""

    @property
    def capabilities(self) -> dict[str, bool]:
        if self.transport is None:
            return {}
        return self.transport.capabilities

    def to_dict(self) -> dict[str, Any]:
        return {
            "printer_id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "connected": self.connected,
            "transport": self.transport_kind,
            "last_error": self.last_error,
            "capabilities": self.capabilities,
            "status": self.status.to_dict(),
        }


class PrinterManager:
    """Point d'entree unique pour parler aux imprimantes.

    Une tache asyncio par imprimante gere connexion, interrogation periodique et
    reconnexion avec temporisation. Les commandes utilisateur passent par
    `command()`, qui traduit les erreurs transport en `PrinterError`.
    """

    def __init__(self) -> None:
        self._runtimes: dict[int, PrinterRuntime] = {}
        self._running = False
        self._settings = get_settings()

    # ------------------------------------------------------------ cycle de vie
    async def start(self) -> None:
        self._running = True
        async with session_scope() as session:
            printers = (await session.execute(select(Printer))).scalars().all()
        for printer in printers:
            await self.add(printer)
        log.info("Parc demarre: %d imprimante(s)", len(self._runtimes))

    async def stop(self) -> None:
        self._running = False
        await asyncio.gather(
            *(self._stop_runtime(runtime) for runtime in list(self._runtimes.values())),
            return_exceptions=True,
        )
        self._runtimes.clear()

    async def add(self, printer: Printer) -> PrinterRuntime:
        await self.remove(printer.id)
        runtime = PrinterRuntime(printer)
        self._runtimes[printer.id] = runtime
        if printer.enabled and self._running:
            runtime.task = asyncio.create_task(
                self._run(runtime), name=f"printer-{printer.id}"
            )
        return runtime

    async def remove(self, printer_id: int) -> None:
        runtime = self._runtimes.pop(printer_id, None)
        if runtime:
            await self._stop_runtime(runtime)

    async def reload(self, printer: Printer) -> PrinterRuntime:
        """Reapplique la configuration (redemarre la connexion)."""
        return await self.add(printer)

    async def _stop_runtime(self, runtime: PrinterRuntime) -> None:
        if runtime.task:
            runtime.task.cancel()
            try:
                await runtime.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            runtime.task = None
        if runtime.transport:
            try:
                await runtime.transport.close()
            except Exception:  # noqa: BLE001 - fermeture best-effort
                pass
            runtime.transport = None
        runtime.connected = False

    # ------------------------------------------------------------ boucle poll
    async def _run(self, runtime: PrinterRuntime) -> None:
        settings = self._settings
        while self._running:
            try:
                if runtime.transport is None:
                    transport_cls = TRANSPORTS.get(runtime.transport_kind)
                    if transport_cls is None:
                        raise PrinterError(f"Protocole inconnu: {runtime.transport_kind}")
                    transport = transport_cls(runtime.config)
                    await asyncio.wait_for(
                        transport.connect(), timeout=settings.connect_timeout + 6
                    )
                    runtime.transport = transport
                    runtime.connected = True
                    runtime.last_error = None
                    await record_event(
                        f"{runtime.name}: connectee ({runtime.transport_kind})",
                        category="printer",
                        printer_id=runtime.id,
                    )

                status = await asyncio.wait_for(runtime.transport.refresh(), timeout=20)
                runtime.status = status
                runtime.connected = True
                await self._sync_cfs(runtime, status.spools)
                await self._check_bed_cold(runtime, status)
                bus.publish("printer.status", runtime.to_dict())
                await asyncio.sleep(settings.poll_interval)

            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - toute panne doit etre rattrapee
                was_connected = runtime.connected
                runtime.connected = False
                runtime.last_error = str(exc)
                runtime.status = PrinterStatus(state=STATE_OFFLINE, state_message=str(exc))
                if runtime.transport:
                    try:
                        await runtime.transport.close()
                    except Exception:  # noqa: BLE001
                        pass
                    runtime.transport = None
                if was_connected:
                    await record_event(
                        f"{runtime.name}: connexion perdue ({exc})",
                        level="warning",
                        category="printer",
                        printer_id=runtime.id,
                    )
                bus.publish("printer.status", runtime.to_dict())
                await asyncio.sleep(self._settings.reconnect_delay)

    async def _sync_cfs(self, runtime: PrinterRuntime, spools: list[SpoolState]) -> None:
        """Repercute l'etat du CFS dans la table des bobines.

        On n'ecrit qu'en cas de changement reel: la boucle tourne toutes les
        deux secondes et SQLite n'a pas besoin de ce trafic.
        """
        if not spools:
            return
        fingerprint = "|".join(
            f"{s.unit}:{s.slot}:{s.material}:{s.color_hex}:{s.empty}:{round(s.remaining_pct or -1)}"
            f":{s.active}"
            for s in sorted(spools, key=lambda s: (s.unit, s.slot))
        )
        if fingerprint == runtime._cfs_fingerprint:
            return
        runtime._cfs_fingerprint = fingerprint

        async with session_scope() as session:
            existing = {
                (row.unit, row.slot): row
                for row in (
                    await session.execute(select(Spool).where(Spool.printer_id == runtime.id))
                ).scalars()
            }
            for state in spools:
                row = existing.get((state.unit, state.slot))
                if row is None:
                    # total_g est fixe explicitement: la valeur par defaut de la
                    # colonne n'existe qu'apres l'INSERT, trop tard pour deduire
                    # le restant en grammes du pourcentage remonte par le CFS.
                    row = Spool(
                        printer_id=runtime.id, unit=state.unit, slot=state.slot, total_g=1000.0
                    )
                    session.add(row)
                row.material = state.material
                row.color_hex = state.color_hex
                row.vendor = state.vendor or row.vendor
                row.active = state.active
                row.empty = state.empty
                row.managed = True
                if state.remaining_g is not None:
                    row.remaining_g = state.remaining_g
                elif state.remaining_pct is not None and row.total_g:
                    row.remaining_g = round(row.total_g * state.remaining_pct / 100.0, 1)
        bus.publish("spools.updated", {"printer_id": runtime.id})

    async def _check_bed_cold(self, runtime: PrinterRuntime, status: PrinterStatus) -> None:
        """Notifie les webhooks `bed_cold` des lors que le plateau est descendu
        sous leur seuil, une fois par webhook depuis le dernier armement
        (voir `arm_bed_cold`, appele a la fin d'un travail)."""
        if not runtime.bed_cold_armed or status.bed_temp <= 0:
            # bed_temp <= 0 = pas encore une vraie lecture (juste apres armement).
            return
        async with session_scope() as session:
            printer = await session.get(Printer, runtime.id)
            if printer is None:
                runtime.bed_cold_armed = False
                return
            webhooks = await matching_webhooks(session, WebhookEvent.BED_COLD, printer)

        pending = [w for w in webhooks if w.id not in runtime.bed_cold_notified]
        if not pending:
            runtime.bed_cold_armed = False
            return
        for webhook in pending:
            if status.bed_temp > webhook.bed_cold_threshold:
                continue
            runtime.bed_cold_notified.add(webhook.id)
            # Tache independante: l'appel HTTP ne doit pas retenir de transaction.
            asyncio.create_task(self._fire_bed_cold(webhook.id, runtime.id, status.bed_temp))
        if len(runtime.bed_cold_notified) >= len(webhooks):
            runtime.bed_cold_armed = False

    async def _fire_bed_cold(self, webhook_id: int, printer_id: int, bed_temp: float) -> None:
        async with session_scope() as session:
            webhook = await session.get(Webhook, webhook_id)
            printer = await session.get(Printer, printer_id)
            if webhook is None or printer is None:
                return
            await fire(session, webhook, bed_cold_payload(printer, bed_temp))

    # ----------------------------------------------------------------- acces
    def get(self, printer_id: int) -> PrinterRuntime | None:
        return self._runtimes.get(printer_id)

    def require(self, printer_id: int) -> PrinterRuntime:
        runtime = self._runtimes.get(printer_id)
        if runtime is None:
            raise PrinterError(f"Imprimante {printer_id} inconnue du gestionnaire")
        return runtime

    def status_of(self, printer_id: int) -> PrinterStatus:
        runtime = self._runtimes.get(printer_id)
        return runtime.status if runtime else PrinterStatus(state=STATE_OFFLINE)

    def snapshot(self) -> list[dict[str, Any]]:
        return [runtime.to_dict() for runtime in self._runtimes.values()]

    def is_free(self, printer_id: int) -> bool:
        runtime = self._runtimes.get(printer_id)
        return bool(runtime and runtime.connected and runtime.status.is_free)

    def arm_bed_cold(self, printer_id: int) -> None:
        """A appeler quand un travail vient de se terminer sur cette machine:
        le prochain cycle de poll commence a guetter le refroidissement du
        plateau pour les webhooks `bed_cold` (voir `_check_bed_cold`)."""
        runtime = self._runtimes.get(printer_id)
        if runtime is None:
            return
        runtime.bed_cold_armed = True
        runtime.bed_cold_notified = set()

    async def command(
        self, printer_id: int, action: Callable[[PrinterTransport], Awaitable[T]]
    ) -> T:
        """Execute une commande sur le transport, en serialisant les acces."""
        runtime = self.require(printer_id)
        if runtime.transport is None or not runtime.connected:
            raise PrinterError(f"{runtime.name} est hors ligne")
        async with runtime._lock:
            try:
                return await action(runtime.transport)
            except PrinterError:
                raise
            except TimeoutError as exc:
                raise PrinterError("Delai depasse par l'imprimante") from exc
            except Exception as exc:  # noqa: BLE001
                raise PrinterError(str(exc)) from exc


manager = PrinterManager()
