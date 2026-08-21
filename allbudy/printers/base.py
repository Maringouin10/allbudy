"""Contrat commun a tous les transports d'imprimante."""
from __future__ import annotations

import abc
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class PrinterError(RuntimeError):
    """Erreur remontee par un transport (reseau, protocole, refus machine)."""


class UnsupportedOperation(PrinterError):
    """Operation non disponible sur ce protocole/firmware."""


# Etats normalises, communs a tous les firmwares.
STATE_OFFLINE = "offline"
STATE_IDLE = "idle"
STATE_PRINTING = "printing"
STATE_PAUSED = "paused"
STATE_COMPLETE = "complete"
STATE_ERROR = "error"
STATE_BUSY = "busy"

BUSY_STATES = {STATE_PRINTING, STATE_PAUSED, STATE_BUSY}


@dataclass(slots=True)
class SpoolState:
    """Etat d'un emplacement de filament tel que rapporte par la machine.

    `unit` vaut -1 pour une bobine externe posee sur le support (pas de CFS).
    """

    unit: int = 0
    slot: int = 0
    material: str = "PLA"
    color_hex: str = "#7f8c8d"
    vendor: str | None = None
    remaining_pct: float | None = None
    remaining_g: float | None = None
    active: bool = False
    empty: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PrinterStatus:
    """Instantane normalise de l'etat d'une imprimante."""

    online: bool = False
    state: str = STATE_OFFLINE
    state_message: str | None = None

    nozzle_temp: float = 0.0
    nozzle_target: float = 0.0
    bed_temp: float = 0.0
    bed_target: float = 0.0
    chamber_temp: float | None = None
    chamber_target: float | None = None

    progress: float = 0.0
    current_layer: int | None = None
    total_layers: int | None = None
    elapsed_time: int | None = None
    remaining_time: int | None = None
    filename: str | None = None

    part_fan: float | None = None
    aux_fan: float | None = None
    chamber_fan: float | None = None
    light_on: bool | None = None

    speed_factor: float | None = None
    flow_factor: float | None = None
    position: dict[str, float] = field(default_factory=dict)

    firmware: str | None = None
    model: str | None = None
    spools: list[SpoolState] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_printing(self) -> bool:
        return self.state == STATE_PRINTING

    @property
    def is_free(self) -> bool:
        """L'imprimante peut-elle accepter un nouveau travail ?"""
        return self.online and self.state in (STATE_IDLE, STATE_COMPLETE)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["spools"] = [s if isinstance(s, dict) else s.to_dict() for s in data["spools"]]
        data["updated_at"] = self.updated_at.isoformat()
        data["is_printing"] = self.is_printing
        data["is_free"] = self.is_free
        return data


@dataclass(slots=True)
class RemoteFile:
    """Fichier present dans le stockage de l'imprimante."""

    name: str
    size: int = 0
    modified: float | None = None
    path: str | None = None


@dataclass(slots=True)
class PrinterConfig:
    """Parametres de connexion, decorreles du modele SQLAlchemy."""

    id: int
    name: str
    host: str
    port: int
    model: str = "K1"
    api_key: str | None = None
    upload_root: str = "gcodes"
    has_cfs: bool = False
    nozzle_diameter: float = 0.4


StatusCallback = Callable[[PrinterStatus], None]


class PrinterTransport(abc.ABC):
    """Interface de pilotage d'une imprimante.

    Les implementations doivent etre tolerantes: un champ absent du firmware ne
    doit jamais faire echouer la lecture d'etat complete.
    """

    kind: str = "base"

    def __init__(self, config: PrinterConfig) -> None:
        self.config = config
        self.status = PrinterStatus()

    # ---- cycle de vie -----------------------------------------------------
    @abc.abstractmethod
    async def connect(self) -> None:
        """Ouvre la connexion. Leve PrinterError en cas d'echec."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Ferme proprement la connexion."""

    @abc.abstractmethod
    async def refresh(self) -> PrinterStatus:
        """Retourne l'etat courant (interrogation ou dernier push recu)."""

    # ---- controle impression ---------------------------------------------
    @abc.abstractmethod
    async def pause(self) -> None: ...

    @abc.abstractmethod
    async def resume(self) -> None: ...

    @abc.abstractmethod
    async def cancel(self) -> None: ...

    @abc.abstractmethod
    async def start_print(self, remote_name: str) -> None: ...

    # ---- fichiers ---------------------------------------------------------
    @abc.abstractmethod
    async def list_files(self) -> list[RemoteFile]: ...

    @abc.abstractmethod
    async def upload(self, local_path: Path, remote_name: str, start: bool = False) -> str:
        """Envoie un fichier sur la machine et retourne son nom distant."""

    async def delete_file(self, remote_name: str) -> None:
        raise UnsupportedOperation("Suppression de fichier non supportee")

    # ---- controle machine -------------------------------------------------
    async def send_gcode(self, script: str) -> None:
        raise UnsupportedOperation("Envoi de G-code brut non supporte")

    async def set_temperature(self, heater: str, value: float) -> None:
        """heater: extruder | bed | chamber."""
        raise UnsupportedOperation("Consigne de temperature non supportee")

    async def set_fan(self, fan: str, speed: float) -> None:
        """speed: 0..100 (%)."""
        raise UnsupportedOperation("Pilotage ventilateur non supporte")

    async def set_light(self, on: bool) -> None:
        raise UnsupportedOperation("Pilotage eclairage non supporte")

    async def home(self, axes: str = "XYZ") -> None:
        raise UnsupportedOperation("Prise d'origine non supportee")

    async def move(self, axis: str, distance: float, speed: float = 3000) -> None:
        raise UnsupportedOperation("Deplacement non supporte")

    async def extrude(self, distance: float, speed: float = 300) -> None:
        raise UnsupportedOperation("Extrusion non supportee")

    async def set_speed_factor(self, percent: float) -> None:
        raise UnsupportedOperation("Facteur de vitesse non supporte")

    async def emergency_stop(self) -> None:
        raise UnsupportedOperation("Arret d'urgence non supporte")

    # ---- CFS --------------------------------------------------------------
    async def get_spools(self) -> list[SpoolState]:
        """Etat des bobines (CFS). Liste vide si la machine n'en expose pas."""
        return list(self.status.spools)

    @property
    def capabilities(self) -> dict[str, bool]:
        """Fonctions reellement disponibles, pour griser l'UI cote client."""
        return {
            "gcode": True,
            "temperature": True,
            "fans": True,
            "light": True,
            "motion": True,
            "upload": True,
            "files": True,
            "cfs": self.config.has_cfs,
            "emergency_stop": True,
        }
