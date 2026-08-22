"""Imprimante virtuelle: pas de machine reelle a piloter.

Utile pour un modele possede mais pas (encore) relie au reseau, ou pour
preparer un fichier a emporter: « imprimer » envoie le G-code sur un depot
NAS configure (voir `Printer.virtual_target_storage_id`) plutot que de le
televerser sur une machine. Comme aucune telemetrie n'existe, le demarrage et
la fin de l'impression sont confirmes a la main (voir `confirm_start` /
`confirm_finished`, appeles depuis l'API sur action de l'utilisateur) au lieu
d'etre observes par la boucle de poll habituelle.
"""
from __future__ import annotations

from datetime import UTC, datetime

from .base import (
    STATE_AWAITING_START,
    STATE_COMPLETE,
    STATE_IDLE,
    STATE_PRINTING,
    PrinterError,
    PrinterStatus,
    PrinterTransport,
    RemoteFile,
    UnsupportedOperation,
)


class VirtualTransport(PrinterTransport):
    kind = "virtual"

    def __init__(self, config) -> None:
        super().__init__(config)
        self._state = STATE_IDLE
        self._filename: str | None = None
        self._progress = 0.0
        self._started_at: datetime | None = None

    async def connect(self) -> None:
        pass  # rien a joindre: toujours "connectee".

    async def close(self) -> None:
        pass

    async def refresh(self) -> PrinterStatus:
        return PrinterStatus(
            online=True,
            state=self._state,
            filename=self._filename,
            progress=self._progress,
            model=self.config.model,
        )

    # ---- cycle « impression » ---------------------------------------------
    async def start_print(self, remote_name: str) -> None:
        """Appele apres l'envoi sur le NAS (voir scheduler._send_job): arme
        l'attente de confirmation, ne lance rien reellement."""
        self._filename = remote_name
        self._state = STATE_AWAITING_START
        self._progress = 0.0
        self._started_at = None

    async def confirm_start(self) -> None:
        if self._state != STATE_AWAITING_START:
            raise PrinterError("Aucune impression en attente de demarrage")
        self._state = STATE_PRINTING
        self._started_at = datetime.now(UTC)

    async def confirm_finished(self) -> None:
        if self._state != STATE_PRINTING:
            raise PrinterError("Aucune impression en cours a terminer")
        self._state = STATE_COMPLETE
        self._progress = 100.0

    async def pause(self) -> None:
        raise UnsupportedOperation("Pas de pause sur une imprimante virtuelle")

    async def resume(self) -> None:
        raise UnsupportedOperation("Pas de reprise sur une imprimante virtuelle")

    async def cancel(self) -> None:
        self._state = STATE_IDLE
        self._filename = None
        self._progress = 0.0

    # ---- fichiers -----------------------------------------------------
    async def list_files(self) -> list[RemoteFile]:
        return []

    async def upload(self, local_path, remote_name: str, start: bool = False) -> str:
        """Ne fait rien: l'envoi reel sur le NAS est fait par le scheduler,
        qui a besoin d'une session BDD pour lire le depot cible configure."""
        return remote_name

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "gcode": False,
            "temperature": False,
            "fans": False,
            "light": False,
            "motion": False,
            "upload": True,
            "files": False,
            "cfs": False,
            "emergency_stop": False,
            "virtual": True,
        }
