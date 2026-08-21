"""Transport LAN proprietaire Creality (WebSocket port 9999).

C'est le protocole que parle Creality Print quand l'imprimante est passee en
mode LAN / developpeur. Il fonctionne sur firmware d'origine, sans root ni
Moonraker, et ne sort jamais du reseau local.

Le protocole n'est pas documente publiquement: il est ici reconstruit par
observation. Les noms de champs varient d'une version de firmware a l'autre,
donc chaque lecture passe par des alias et retombe sur une valeur neutre si le
champ est absent. Quand Moonraker est accessible, il reste le transport a
privilegier: il est plus complet et plus stable.

Echanges principaux:

  <- {"nozzleTemp":"210", "bedTemp0":"60", "printProgress":42, "state":1, ...}
  -> {"method":"set","params":{"pause":1}}
  -> {"method":"set","params":{"opGcodeFile":"printprt:/usr/data/printer_data/gcodes/x.gcode"}}
  -> {"method":"get","params":{"reqGcodeFile":1}}
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import websockets

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
from .parsing import as_float, as_int, parse_cfs_payload

log = logging.getLogger("allbudy.creality_lan")

#: Repertoire des G-code sur les machines Creality OS.
GCODE_DIR = "/usr/data/printer_data/gcodes"
HEARTBEAT_INTERVAL = 4.0

#: Alias de champs rencontres selon les versions de firmware.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "nozzle_temp": ("nozzleTemp", "nozzleTemp0", "curNozzleTemp"),
    "nozzle_target": ("targetNozzleTemp", "targetNozzleTemp0", "nozzleTempControl"),
    "bed_temp": ("bedTemp0", "bedTemp", "curBedTemp"),
    "bed_target": ("targetBedTemp0", "targetBedTemp", "bedTempControl"),
    "chamber_temp": ("boxTemp", "chamberTemp", "caseTemp"),
    "chamber_target": ("targetBoxTemp", "targetChamberTemp"),
    "progress": ("printProgress", "dProgress"),
    "layer": ("layer", "curLayer"),
    "total_layers": ("TotalLayer", "totalLayer"),
    "elapsed": ("printJobTime", "usedMaterialTime"),
    "remaining": ("printLeftTime", "leftTime"),
    "filename": ("printFileName", "curFileName", "fileName"),
    "part_fan": ("fan", "modelFanPct"),
    "aux_fan": ("fanAuxiliary", "auxiliaryFanPct"),
    "chamber_fan": ("fanCase", "caseFanPct"),
    "light": ("lightSw", "LEDSwitch"),
    "state": ("state", "printStatus"),
    "paused": ("pause", "printPause"),
    "error": ("err", "error", "errcode"),
    "model": ("model", "modelVersion"),
    "firmware": ("modelVersion", "softVersion", "version"),
    "speed_factor": ("curFeedratePct", "feedratePct"),
}

#: Valeurs du champ `state` observees sur K1/K2.
_STATE_MAP = {0: STATE_IDLE, 1: STATE_PRINTING, 2: STATE_COMPLETE, 3: STATE_ERROR, 4: STATE_PAUSED}


def _pick(payload: dict[str, Any], key: str) -> Any:
    for alias in _FIELD_ALIASES.get(key, ()):
        if alias in payload and payload[alias] not in ("", None):
            return payload[alias]
    return None


class CrealityLanTransport(PrinterTransport):
    kind = "creality_lan"

    def __init__(self, config) -> None:
        super().__init__(config)
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._reader: asyncio.Task[None] | None = None
        self._heartbeat: asyncio.Task[None] | None = None
        self._payload: dict[str, Any] = {}
        self._files: list[RemoteFile] = []
        self._files_event = asyncio.Event()
        self._connected = asyncio.Event()

    @property
    def _ws_url(self) -> str:
        return f"ws://{self.config.host}:{self.config.port or 9999}"

    @property
    def _http_url(self) -> str:
        # Le televersement passe par le serveur web embarque (port 80).
        return f"http://{self.config.host}"

    # ---------------------------------------------------------- cycle de vie
    async def connect(self) -> None:
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(self._ws_url, ping_interval=None, max_size=8 << 20), timeout=8.0
            )
        except (TimeoutError, OSError, websockets.WebSocketException) as exc:
            raise PrinterError(f"Connexion LAN Creality impossible: {exc}") from exc

        self._reader = asyncio.create_task(self._read_loop())
        self._heartbeat = asyncio.create_task(self._heartbeat_loop())
        try:
            # Le firmware n'envoie sa premiere trame qu'apres une sollicitation.
            await self._send({"method": "get", "params": {"reqPrinterPara": 1}})
            await asyncio.wait_for(self._connected.wait(), timeout=8.0)
        except TimeoutError as exc:
            await self.close()
            raise PrinterError("Aucune trame recue: le mode LAN est-il actif ?") from exc

    async def close(self) -> None:
        for task in (self._heartbeat, self._reader):
            if task:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._heartbeat = self._reader = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001 - fermeture best-effort
                pass
            self._ws = None
        self._connected.clear()

    async def _send(self, message: dict[str, Any]) -> None:
        if self._ws is None:
            raise PrinterError("Imprimante non connectee")
        try:
            await self._ws.send(json.dumps(message))
        except websockets.WebSocketException as exc:
            raise PrinterError(f"Envoi impossible: {exc}") from exc

    async def _set(self, params: dict[str, Any]) -> None:
        await self._send({"method": "set", "params": params})

    async def _heartbeat_loop(self) -> None:
        """Sans battement regulier, le firmware ferme la session au bout de ~10 s."""
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                await self._send({"ModeCode": "heart_beat", "msg": int(time.time())})
        except asyncio.CancelledError:
            raise
        except PrinterError:
            return

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("ModeCode") == "heart_beat":
                    continue
                # Certains firmwares encapsulent l'etat dans "params".
                params = message.get("params")
                payload = params if isinstance(params, dict) else message
                self._payload.update(payload)
                self._connected.set()
                if "retGcodeFileInfo" in payload:
                    self._ingest_file_list(payload["retGcodeFileInfo"])
        except websockets.WebSocketException as exc:
            log.info("WebSocket LAN %s ferme: %s", self.config.name, exc)
        finally:
            self._connected.clear()

    def _ingest_file_list(self, info: Any) -> None:
        entries: list[Any] = []
        if isinstance(info, dict):
            for key in ("file", "files", "fileInfo"):
                if isinstance(info.get(key), list):
                    entries = info[key]
                    break
        elif isinstance(info, list):
            entries = info

        files: list[RemoteFile] = []
        for entry in entries:
            if isinstance(entry, str):
                files.append(RemoteFile(name=entry.rsplit("/", 1)[-1], path=entry))
            elif isinstance(entry, dict):
                path = entry.get("path") or entry.get("name") or entry.get("filename") or ""
                if not path:
                    continue
                files.append(
                    RemoteFile(
                        name=str(path).rsplit("/", 1)[-1],
                        size=as_int(entry.get("size")),
                        modified=as_float(entry.get("time") or entry.get("modified")) or None,
                        path=str(path),
                    )
                )
        self._files = files
        self._files_event.set()

    # ----------------------------------------------------------------- etat
    async def refresh(self) -> PrinterStatus:
        if self._ws is None or not self._connected.is_set():
            raise PrinterError("Connexion LAN perdue")
        payload = self._payload
        status = PrinterStatus(
            online=True,
            state=self._map_state(payload),
            nozzle_temp=round(as_float(_pick(payload, "nozzle_temp")), 1),
            nozzle_target=round(as_float(_pick(payload, "nozzle_target")), 1),
            bed_temp=round(as_float(_pick(payload, "bed_temp")), 1),
            bed_target=round(as_float(_pick(payload, "bed_target")), 1),
            progress=round(as_float(_pick(payload, "progress")), 1),
            filename=(str(_pick(payload, "filename")).rsplit("/", 1)[-1] or None)
            if _pick(payload, "filename")
            else None,
            firmware=str(_pick(payload, "firmware") or "") or None,
            model=str(_pick(payload, "model") or self.config.model) or None,
            updated_at=datetime.now(UTC),
        )

        chamber = _pick(payload, "chamber_temp")
        if chamber is not None:
            status.chamber_temp = round(as_float(chamber), 1)
            chamber_target = _pick(payload, "chamber_target")
            if chamber_target is not None:
                status.chamber_target = round(as_float(chamber_target), 1)

        layer = _pick(payload, "layer")
        total = _pick(payload, "total_layers")
        status.current_layer = as_int(layer) if layer is not None else None
        status.total_layers = as_int(total) if total is not None else None
        elapsed = _pick(payload, "elapsed")
        remaining = _pick(payload, "remaining")
        status.elapsed_time = as_int(elapsed) if elapsed is not None else None
        status.remaining_time = as_int(remaining) if remaining is not None else None

        for field in ("part_fan", "aux_fan", "chamber_fan"):
            value = _pick(payload, field)
            if value is not None:
                # Selon les firmwares: interrupteur 0/1 ou pourcentage 0..100.
                numeric = as_float(value)
                setattr(status, field, 100.0 if numeric == 1 else round(numeric, 1))

        light = _pick(payload, "light")
        if light is not None:
            status.light_on = as_float(light) > 0
        speed = _pick(payload, "speed_factor")
        if speed is not None:
            status.speed_factor = round(as_float(speed), 1)

        error = _pick(payload, "error")
        if error not in (None, 0, "0"):
            status.state = STATE_ERROR
            status.state_message = f"Erreur firmware: {error}"

        status.spools = self._parse_spools()
        if status.spools:
            self.config.has_cfs = True
        status.raw = {"state": payload.get("state"), "err": payload.get("err")}
        self.status = status
        return status

    def _map_state(self, payload: dict[str, Any]) -> str:
        paused = _pick(payload, "paused")
        if paused is not None and as_int(paused) == 1:
            return STATE_PAUSED
        raw_state = _pick(payload, "state")
        if raw_state is not None:
            mapped = _STATE_MAP.get(as_int(raw_state))
            if mapped:
                return mapped
        return STATE_PRINTING if as_float(_pick(payload, "progress")) > 0 else STATE_IDLE

    def _parse_spools(self) -> list[SpoolState]:
        for key in ("boxsInfo", "boxInfo", "cfsInfo", "materialInfo"):
            payload = self._payload.get(key)
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    continue
            spools = parse_cfs_payload(payload)
            if spools:
                return spools
        return []

    # ------------------------------------------------------------- commandes
    async def pause(self) -> None:
        await self._set({"pause": 1})

    async def resume(self) -> None:
        await self._set({"pause": 0})

    async def cancel(self) -> None:
        await self._set({"stop": 1})

    async def start_print(self, remote_name: str) -> None:
        name = remote_name.rsplit("/", 1)[-1]
        await self._set({"opGcodeFile": f"printprt:{GCODE_DIR}/{name}"})

    async def send_gcode(self, script: str) -> None:
        for line in script.splitlines():
            line = line.strip()
            if line:
                await self._set({"gcodeCmd": line})

    async def emergency_stop(self) -> None:
        await self.send_gcode("M112")

    async def set_temperature(self, heater: str, value: float) -> None:
        value = max(0.0, float(value))
        if heater == "extruder":
            await self._set({"nozzleTempControl": int(value)})
        elif heater == "bed":
            await self._set({"bedTempControl": {"num": 0, "val": int(value)}})
        elif heater == "chamber":
            await self._set({"boxTempControl": int(value)})
        else:
            raise PrinterError(f"Chauffe inconnue: {heater}")

    async def set_fan(self, fan: str, speed: float) -> None:
        key = {"part": "fan", "aux": "fanAuxiliary", "chamber": "fanCase"}.get(fan)
        if not key:
            raise PrinterError(f"Ventilateur inconnu: {fan}")
        # Le firmware LAN n'expose qu'un interrupteur pour ces ventilateurs.
        await self._set({key: 1 if float(speed) > 0 else 0})

    async def set_light(self, on: bool) -> None:
        await self._set({"lightSw": 1 if on else 0})

    async def home(self, axes: str = "XYZ") -> None:
        axes = "".join(a for a in axes.upper() if a in "XYZ")
        await self.send_gcode(f"G28 {' '.join(axes)}".strip() if axes else "G28")

    async def move(self, axis: str, distance: float, speed: float = 3000) -> None:
        axis = axis.upper()
        if axis not in ("X", "Y", "Z"):
            raise PrinterError(f"Axe invalide: {axis}")
        await self.send_gcode(f"G91\nG1 {axis}{distance:.3f} F{speed:.0f}\nG90")

    async def extrude(self, distance: float, speed: float = 300) -> None:
        await self.send_gcode(f"M83\nG1 E{distance:.2f} F{speed:.0f}")

    async def set_speed_factor(self, percent: float) -> None:
        await self.send_gcode(f"M220 S{max(10, min(300, int(percent)))}")

    # -------------------------------------------------------------- fichiers
    async def list_files(self) -> list[RemoteFile]:
        self._files_event.clear()
        await self._send({"method": "get", "params": {"reqGcodeFile": 1}})
        try:
            await asyncio.wait_for(self._files_event.wait(), timeout=10.0)
        except TimeoutError:
            log.warning("Liste de fichiers non renvoyee par %s", self.config.name)
        return list(self._files)

    async def upload(self, local_path: Path, remote_name: str, start: bool = False) -> str:
        path = Path(local_path)
        if not path.exists():
            raise PrinterError(f"Fichier introuvable: {path}")
        name = remote_name.rsplit("/", 1)[-1]
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=6.0)) as client:
            with path.open("rb") as handle:
                files = {"file": (name, handle, "application/octet-stream")}
                try:
                    response = await client.post(
                        f"{self._http_url}/upload/{name}",
                        files=files,
                        headers={"Connection": "keep-alive"},
                    )
                except httpx.HTTPError as exc:
                    raise PrinterError(f"Televersement impossible: {exc}") from exc
            if response.status_code >= 400:
                raise PrinterError(
                    f"Televersement refuse ({response.status_code}): {response.text[:200]}"
                )
        if start:
            await self.start_print(name)
        return name

    async def delete_file(self, remote_name: str) -> None:
        name = remote_name.rsplit("/", 1)[-1]
        await self._set({"opGcodeFile": f"deleteprt:{GCODE_DIR}/{name}"})

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "gcode": True,
            "temperature": True,
            "fans": True,
            "light": True,
            "motion": True,
            "upload": True,
            "files": True,
            "cfs": bool(self.status.spools) or self.config.has_cfs,
            "emergency_stop": True,
            "push_updates": True,
        }
