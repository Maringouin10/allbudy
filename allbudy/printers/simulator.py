"""Imprimante simulee.

Permet de faire tourner AllBudy sans materiel: demonstration, developpement de
l'interface et tests d'integration de la file d'attente. Le comportement
thermique est volontairement simpliste mais coherent (montee en temperature
progressive, avance des couches, fin de travail).
"""
from __future__ import annotations

import asyncio
import random
import time
from datetime import UTC, datetime
from pathlib import Path

from .base import (
    STATE_COMPLETE,
    STATE_ERROR,
    STATE_IDLE,
    STATE_PAUSED,
    STATE_PRINTING,
    PrinterError,
    PrinterStatus,
    PrinterTransport,
    RemoteFile,
    SpoolState,
)

#: Duree simulee d'une impression, en secondes.
DEFAULT_PRINT_SECONDS = 180.0
DEFAULT_LAYERS = 120

_PALETTE = [
    ("PLA", "#E74C3C", "Rouge"),
    ("PLA", "#27AE60", "Vert"),
    ("PETG", "#2980B9", "Bleu"),
    ("ABS", "#2C3E50", "Noir"),
]


class SimulatorTransport(PrinterTransport):
    kind = "simulator"

    def __init__(self, config) -> None:
        super().__init__(config)
        self._files: dict[str, RemoteFile] = {}
        self._state = STATE_IDLE
        self._filename: str | None = None
        self._started_at: float | None = None
        self._paused_at: float | None = None
        self._paused_total = 0.0
        self._duration = DEFAULT_PRINT_SECONDS
        self._duration_override: float | None = None
        self._nozzle = 25.0
        self._bed = 25.0
        self._nozzle_target = 0.0
        self._bed_target = 0.0
        self._chamber = 26.0
        self._light = True
        self._last_tick = time.monotonic()
        self._spools: list[SpoolState] = []
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        seed = f"{self.config.id}:{self.config.name}"
        rng = random.Random(seed)
        self._spools = [
            SpoolState(
                unit=0,
                slot=index,
                material=material,
                color_hex=color,
                vendor="Creality",
                remaining_pct=rng.randint(20, 100),
                active=index == 0,
            )
            for index, (material, color, _name) in enumerate(_PALETTE)
        ]
        self.config.has_cfs = True
        self._last_tick = time.monotonic()

    async def close(self) -> None:
        self._state = STATE_IDLE

    # ------------------------------------------------------------------ tick
    def _tick(self) -> None:
        """Avance la simulation depuis le dernier appel."""
        now = time.monotonic()
        delta = max(0.0, now - self._last_tick)
        self._last_tick = now

        # Inertie thermique: 60 % de l'ecart resorbe toutes les ~10 s.
        for attr, target in (("_nozzle", self._nozzle_target), ("_bed", self._bed_target)):
            current = getattr(self, attr)
            ambient = 25.0
            goal = target if target > 0 else ambient
            rate = 2.5 if attr == "_nozzle" else 0.8
            step = min(abs(goal - current), rate * delta)
            setattr(self, attr, round(current + step * (1 if goal > current else -1), 1))

        if self._state == STATE_PRINTING:
            self._chamber = min(45.0, self._chamber + 0.05 * delta)
            if self._elapsed() >= self._duration:
                self._finish()
        else:
            self._chamber = max(25.0, self._chamber - 0.05 * delta)

    def _elapsed(self) -> float:
        if self._started_at is None:
            return 0.0
        end = self._paused_at if self._paused_at is not None else time.monotonic()
        return max(0.0, end - self._started_at - self._paused_total)

    def _finish(self) -> None:
        self._state = STATE_COMPLETE
        self._nozzle_target = 0.0
        self._bed_target = 0.0
        for spool in self._spools:
            if spool.active and spool.remaining_pct is not None:
                spool.remaining_pct = max(0.0, spool.remaining_pct - random.uniform(1.0, 4.0))
                spool.empty = spool.remaining_pct <= 0

    async def refresh(self) -> PrinterStatus:
        async with self._lock:
            self._tick()
            elapsed = self._elapsed()
            progress = (
                min(100.0, elapsed / self._duration * 100.0)
                if self._state in (STATE_PRINTING, STATE_PAUSED)
                else (100.0 if self._state == STATE_COMPLETE else 0.0)
            )
            layer = int(progress / 100 * DEFAULT_LAYERS) or None
            status = PrinterStatus(
                online=True,
                state=self._state,
                nozzle_temp=self._nozzle,
                nozzle_target=self._nozzle_target,
                bed_temp=self._bed,
                bed_target=self._bed_target,
                chamber_temp=round(self._chamber, 1),
                progress=round(progress, 1),
                current_layer=layer,
                total_layers=DEFAULT_LAYERS if self._filename else None,
                elapsed_time=int(elapsed) or None,
                remaining_time=int(max(0.0, self._duration - elapsed))
                if self._state in (STATE_PRINTING, STATE_PAUSED)
                else None,
                filename=self._filename,
                part_fan=100.0 if self._state == STATE_PRINTING else 0.0,
                aux_fan=60.0 if self._state == STATE_PRINTING else 0.0,
                light_on=self._light,
                speed_factor=100.0,
                flow_factor=100.0,
                position={"x": 110.0, "y": 110.0, "z": round((layer or 0) * 0.2, 2)},
                firmware="simulateur-1.0",
                model=self.config.model,
                spools=[
                    SpoolState(**{**s.to_dict(), "remaining_pct": round(s.remaining_pct or 0, 1)})
                    for s in self._spools
                ],
                updated_at=datetime.now(UTC),
            )
            self.status = status
            return status

    # ------------------------------------------------------------- commandes
    async def start_print(self, remote_name: str) -> None:
        async with self._lock:
            if self._state in (STATE_PRINTING, STATE_PAUSED):
                raise PrinterError("Une impression est deja en cours")
            name = remote_name.rsplit("/", 1)[-1]
            if name not in self._files:
                raise PrinterError(f"Fichier absent de la machine: {name}")
            self._filename = name
            self._state = STATE_PRINTING
            self._started_at = time.monotonic()
            self._paused_at = None
            self._paused_total = 0.0
            self._nozzle_target = 220.0
            self._bed_target = 60.0

    async def pause(self) -> None:
        async with self._lock:
            if self._state != STATE_PRINTING:
                raise PrinterError("Aucune impression en cours")
            self._state = STATE_PAUSED
            self._paused_at = time.monotonic()

    async def resume(self) -> None:
        async with self._lock:
            if self._state != STATE_PAUSED:
                raise PrinterError("L'impression n'est pas en pause")
            if self._paused_at is not None:
                self._paused_total += time.monotonic() - self._paused_at
            self._paused_at = None
            self._state = STATE_PRINTING

    async def cancel(self) -> None:
        async with self._lock:
            self._state = STATE_IDLE
            self._filename = None
            self._started_at = None
            self._paused_at = None
            self._nozzle_target = 0.0
            self._bed_target = 0.0

    async def send_gcode(self, script: str) -> None:
        for line in script.splitlines():
            line = line.strip().upper()
            if line.startswith("M104") and "S" in line:
                self._nozzle_target = float(line.split("S")[1].split()[0])
            elif line.startswith("M140") and "S" in line:
                self._bed_target = float(line.split("S")[1].split()[0])
            elif line == "M112":
                self._state = STATE_ERROR

    async def set_temperature(self, heater: str, value: float) -> None:
        value = max(0.0, min(350.0, float(value)))
        if heater == "extruder":
            self._nozzle_target = value
        elif heater == "bed":
            self._bed_target = min(120.0, value)
        elif heater == "chamber":
            pass
        else:
            raise PrinterError(f"Chauffe inconnue: {heater}")

    async def set_fan(self, fan: str, speed: float) -> None:
        if fan not in ("part", "aux", "chamber"):
            raise PrinterError(f"Ventilateur inconnu: {fan}")

    async def set_light(self, on: bool) -> None:
        self._light = bool(on)

    async def home(self, axes: str = "XYZ") -> None:
        return None

    async def move(self, axis: str, distance: float, speed: float = 3000) -> None:
        if axis.upper() not in ("X", "Y", "Z"):
            raise PrinterError(f"Axe invalide: {axis}")

    async def extrude(self, distance: float, speed: float = 300) -> None:
        return None

    async def set_speed_factor(self, percent: float) -> None:
        return None

    async def emergency_stop(self) -> None:
        async with self._lock:
            self._state = STATE_ERROR
            self._nozzle_target = 0.0
            self._bed_target = 0.0

    # -------------------------------------------------------------- fichiers
    async def list_files(self) -> list[RemoteFile]:
        return list(self._files.values())

    async def upload(self, local_path: Path, remote_name: str, start: bool = False) -> str:
        path = Path(local_path)
        name = remote_name.rsplit("/", 1)[-1]
        size = path.stat().st_size if path.exists() else 0
        self._files[name] = RemoteFile(name=name, size=size, modified=time.time(), path=name)
        if self._duration_override is None:
            # Duree proportionnelle a la taille, bornee pour rester demonstratif.
            self._duration = max(45.0, min(600.0, size / 250_000))
        if start:
            await self.start_print(name)
        return name

    async def delete_file(self, remote_name: str) -> None:
        self._files.pop(remote_name.rsplit("/", 1)[-1], None)

    def set_print_duration(self, seconds: float) -> None:
        """Fixe la duree simulee (utilise par les tests).

        La consigne prime sur l'estimation faite a partir de la taille du
        fichier, y compris pour les televersements suivants.
        """
        self._duration_override = max(0.5, float(seconds))
        self._duration = self._duration_override
