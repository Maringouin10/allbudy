"""Transport Moonraker/Klipper.

Couvre les Creality tournant sous Creality OS (K1, K1C, K1 Max, K2 Plus,
Ender-3 V3 KE/Plus, Sonic Pad) une fois Moonraker accessible sur le reseau
local, ainsi que n'importe quelle machine Klipper generique.

Strategie: un WebSocket JSON-RPC porte l'etat en push (`printer.objects.subscribe`)
et l'API HTTP porte les televersements et les commandes ponctuelles. Si le
WebSocket est indisponible, on retombe automatiquement sur une interrogation
HTTP periodique.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import websockets

from .base import (
    STATE_BUSY,
    STATE_COMPLETE,
    STATE_ERROR,
    STATE_IDLE,
    STATE_PAUSED,
    STATE_PRINTING,
    FanState,
    PrinterError,
    PrinterStatus,
    PrinterTransport,
    RemoteFile,
    SpoolState,
)
from .parsing import as_float as _as_float
from .parsing import parse_cfs_payload

log = logging.getLogger("allbudy.moonraker")

#: Objets Klipper interroges quand ils existent sur la machine.
_CORE_OBJECTS = {
    "webhooks": None,
    "print_stats": None,
    "display_status": None,
    "virtual_sdcard": None,
    "extruder": None,
    "heater_bed": None,
    "toolhead": None,
    "gcode_move": None,
    "fan": None,
}

#: Noms d'objets connus pour la chambre, par ordre de preference.
_CHAMBER_HINTS = (
    "heater_generic chamber_heater",
    "temperature_sensor chamber_temp",
    "temperature_sensor chamber",
    "temperature_sensor Chamber_Temp",
)

#: Objets ventilateurs auxiliaires (noms Creality K1/K2).
_AUX_FAN_HINTS = ("fan_generic auxiliary_cooling_fan", "fan_generic aux_fan")
_CHAMBER_FAN_HINTS = ("fan_generic chamber_circulation_fan", "fan_generic chamber_fan")
_LIGHT_HINTS = ("output_pin caselight", "output_pin LED", "output_pin light", "led chamber_light")

#: Objets exposant le CFS selon les versions de firmware Creality.
#: Une instance nommee (ex: config Klipper `[cfs cfs0]`) apparait comme
#: "cfs cfs0" dans printer.objects.list: on teste egalement en prefixe/mot-cle.
_CFS_HINTS = ("box", "filament_hub", "cfs", "material_box", "materialbox")
_CFS_KEYWORDS = ("cfs", "material_box", "materialbox", "filament_hub", "box")

_KLIPPER_STATE_MAP = {
    "standby": STATE_IDLE,
    "printing": STATE_PRINTING,
    "paused": STATE_PAUSED,
    "complete": STATE_COMPLETE,
    "cancelled": STATE_IDLE,
    "error": STATE_ERROR,
}


def _deep_merge(target: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value


class MoonrakerTransport(PrinterTransport):
    kind = "moonraker"

    def __init__(self, config) -> None:
        super().__init__(config)
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._reader: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._rpc_id = 0
        self._objects: dict[str, Any] = {}
        self._available: list[str] = []
        self._chamber_key: str | None = None
        self._aux_fan_key: str | None = None
        self._chamber_fan_key: str | None = None
        self._light_key: str | None = None
        self._cfs_key: str | None = None
        self._extra_fans: list[str] = []
        self._client: httpx.AsyncClient | None = None
        self._ws_ok = False

    # ------------------------------------------------------------------ HTTP
    @property
    def _base_url(self) -> str:
        return f"http://{self.config.host}:{self.config.port}"

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"X-Api-Key": self.config.api_key} if self.config.api_key else {}
            self._client = httpx.AsyncClient(
                base_url=self._base_url, headers=headers, timeout=httpx.Timeout(20.0, connect=6.0)
            )
        return self._client

    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        try:
            response = await self._http().request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise PrinterError(f"Moonraker injoignable ({exc.__class__.__name__}): {exc}") from exc
        if response.status_code >= 400:
            detail = response.text[:300]
            raise PrinterError(f"Moonraker a repondu {response.status_code}: {detail}")
        if response.headers.get("content-type", "").startswith("application/json"):
            return response.json()
        return response.text

    # ------------------------------------------------------------- WebSocket
    @property
    def _ws_url(self) -> str:
        url = f"ws://{self.config.host}:{self.config.port}/websocket"
        if self.config.api_key:
            url += f"?token={self.config.api_key}"
        return url

    async def _rpc(self, method: str, params: dict[str, Any] | None = None, timeout: float = 10.0):
        if self._ws is None:
            raise PrinterError("WebSocket Moonraker non connecte")
        self._rpc_id += 1
        rpc_id = self._rpc_id
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[rpc_id] = future
        payload = {"jsonrpc": "2.0", "method": method, "id": rpc_id}
        if params is not None:
            payload["params"] = params
        try:
            await self._ws.send(json.dumps(payload))
            return await asyncio.wait_for(future, timeout=timeout)
        except (TimeoutError, websockets.WebSocketException) as exc:
            raise PrinterError(f"Echec RPC {method}: {exc}") from exc
        finally:
            self._pending.pop(rpc_id, None)

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if "id" in message and message["id"] in self._pending:
                    future = self._pending[message["id"]]
                    if not future.done():
                        if "error" in message:
                            future.set_exception(
                                PrinterError(str(message["error"].get("message", message["error"])))
                            )
                        else:
                            future.set_result(message.get("result"))
                    continue
                method = message.get("method")
                if method == "notify_status_update":
                    params = message.get("params") or []
                    if params and isinstance(params[0], dict):
                        _deep_merge(self._objects, params[0])
                elif method in ("notify_klippy_disconnected", "notify_klippy_shutdown"):
                    self._objects.setdefault("webhooks", {})["state"] = "shutdown"
        except websockets.WebSocketException as exc:
            log.info("WebSocket Moonraker %s ferme: %s", self.config.name, exc)
        finally:
            self._ws_ok = False
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(PrinterError("WebSocket ferme"))
            self._pending.clear()

    # ---------------------------------------------------------- cycle de vie
    async def connect(self) -> None:
        # L'API HTTP fait foi pour la disponibilite: elle repond meme quand
        # Klipper est en erreur, ce qui permet d'afficher un etat utile.
        info = await self._request("GET", "/printer/info")
        if isinstance(info, dict):
            result = info.get("result", info)
            self.status.firmware = result.get("software_version")
        try:
            objects = await self._request("GET", "/printer/objects/list")
            self._available = (objects.get("result") or {}).get("objects", [])
        except PrinterError:
            self._available = []
        self._detect_objects()

        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(
                    self._ws_url, ping_interval=20, ping_timeout=20, max_size=8 << 20
                ),
                timeout=8.0,
            )
            self._reader = asyncio.create_task(self._read_loop())
            result = await self._rpc("printer.objects.subscribe", {"objects": self._subscription()})
            if isinstance(result, dict):
                self._objects = result.get("status", {}) or {}
            self._ws_ok = True
        except (TimeoutError, OSError, websockets.WebSocketException, PrinterError) as exc:
            log.info(
                "WebSocket indisponible pour %s (%s) - repli sur interrogation HTTP",
                self.config.name,
                exc,
            )
            await self._close_ws()
            await self._http_query()

    def _detect_objects(self) -> None:
        available = set(self._available)

        def pick(hints: tuple[str, ...], prefix: str | None = None) -> str | None:
            for hint in hints:
                if hint in available:
                    return hint
            if prefix:
                for name in self._available:
                    if name.startswith(prefix):
                        return name
            return None

        self._chamber_key = pick(_CHAMBER_HINTS)
        if not self._chamber_key:
            for name in self._available:
                if "chamber" in name.lower() and name.startswith(
                    ("temperature_sensor", "heater_generic")
                ):
                    self._chamber_key = name
                    break
        self._aux_fan_key = pick(_AUX_FAN_HINTS)
        self._chamber_fan_key = pick(_CHAMBER_FAN_HINTS)
        self._light_key = pick(_LIGHT_HINTS)
        self._cfs_key = pick(_CFS_HINTS) or self._find_cfs_key()
        if self._cfs_key:
            self.config.has_cfs = True
            log.info("%s: CFS detecte via l'objet '%s'", self.config.name, self._cfs_key)
        elif self._available:
            log.info(
                "%s: aucun CFS detecte parmi les objets Klipper exposes (%s)",
                self.config.name,
                ", ".join(sorted(self._available)),
            )

        # Tout ventilateur restant est remonte en lecture seule: le ventilateur
        # de tete par exemple est asservi au firmware, on l'affiche sans le piloter.
        known = {"fan", self._aux_fan_key, self._chamber_fan_key}
        self._extra_fans = [
            name
            for name in self._available
            if name.startswith(("fan_generic ", "heater_fan ", "controller_fan "))
            and name not in known
        ]

    def _find_cfs_key(self) -> str | None:
        """Repli quand aucun nom exact ne correspond.

        Une instance nommee (config Klipper `[cfs cfs0]`) apparait comme
        "cfs cfs0": on ne compare que le type (premier mot), jamais le nom
        choisi par l'utilisateur, pour eviter un faux positif sur un objet
        sans rapport dont le nom contiendrait par hasard un mot-cle.
        """
        for name in self._available:
            object_type = name.split(" ", 1)[0].lower()
            if any(keyword in object_type for keyword in _CFS_KEYWORDS):
                return name
        return None

    def _subscription(self) -> dict[str, Any]:
        objects = dict(_CORE_OBJECTS)
        for key in (
            self._chamber_key,
            self._aux_fan_key,
            self._chamber_fan_key,
            self._light_key,
            self._cfs_key,
            *self._extra_fans,
        ):
            if key:
                objects[key] = None
        if self._available:
            objects = {name: None for name in objects if name in self._available} or objects
        return objects

    async def _close_ws(self) -> None:
        if self._reader:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._reader = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001 - fermeture best-effort
                pass
            self._ws = None
        self._ws_ok = False

    async def close(self) -> None:
        await self._close_ws()
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _http_query(self) -> None:
        params = dict.fromkeys(self._subscription(), "")
        query = "&".join(k if not v else f"{k}={v}" for k, v in params.items())
        data = await self._request("GET", f"/printer/objects/query?{query}")
        if isinstance(data, dict):
            self._objects = (data.get("result") or {}).get("status", {}) or {}

    # ----------------------------------------------------------------- etat
    async def refresh(self) -> PrinterStatus:
        if not self._ws_ok:
            await self._http_query()
        self.status = self._build_status()
        return self.status

    def _build_status(self) -> PrinterStatus:
        obj = self._objects
        print_stats = obj.get("print_stats") or {}
        display = obj.get("display_status") or {}
        sdcard = obj.get("virtual_sdcard") or {}
        extruder = obj.get("extruder") or {}
        bed = obj.get("heater_bed") or {}
        toolhead = obj.get("toolhead") or {}
        gcode_move = obj.get("gcode_move") or {}
        webhooks = obj.get("webhooks") or {}

        klipper_state = str(print_stats.get("state") or "").lower()
        state = _KLIPPER_STATE_MAP.get(klipper_state, STATE_IDLE)
        message = print_stats.get("message") or None
        if webhooks.get("state") in ("shutdown", "error"):
            state = STATE_ERROR
            message = webhooks.get("state_message") or message
        elif webhooks.get("state") == "startup":
            state = STATE_BUSY

        progress = _as_float(display.get("progress") or sdcard.get("progress")) * 100.0

        info = print_stats.get("info") or {}
        current_layer = info.get("current_layer")
        total_layers = info.get("total_layer")

        print_duration = _as_float(print_stats.get("print_duration"))
        remaining: int | None = None
        if progress > 1 and print_duration > 0:
            remaining = max(0, int(print_duration * (100.0 - progress) / progress))

        status = PrinterStatus(
            online=True,
            state=state,
            state_message=message,
            nozzle_temp=round(_as_float(extruder.get("temperature")), 1),
            nozzle_target=round(_as_float(extruder.get("target")), 1),
            bed_temp=round(_as_float(bed.get("temperature")), 1),
            bed_target=round(_as_float(bed.get("target")), 1),
            progress=round(progress, 1),
            current_layer=int(current_layer) if isinstance(current_layer, (int, float)) else None,
            total_layers=int(total_layers) if isinstance(total_layers, (int, float)) else None,
            elapsed_time=int(print_duration) or None,
            remaining_time=remaining,
            filename=print_stats.get("filename") or None,
            part_fan=round(_as_float((obj.get("fan") or {}).get("speed")) * 100, 1)
            if "fan" in obj
            else None,
            speed_factor=round(_as_float(gcode_move.get("speed_factor"), 1.0) * 100, 1) or None,
            flow_factor=round(_as_float(gcode_move.get("extrude_factor"), 1.0) * 100, 1) or None,
            firmware=self.status.firmware,
            model=self.config.model,
            updated_at=datetime.now(UTC),
        )

        if self._chamber_key and self._chamber_key in obj:
            chamber = obj[self._chamber_key]
            status.chamber_temp = round(_as_float(chamber.get("temperature")), 1)
            if "target" in chamber:
                status.chamber_target = round(_as_float(chamber.get("target")), 1)
        if self._aux_fan_key and self._aux_fan_key in obj:
            status.aux_fan = round(_as_float(obj[self._aux_fan_key].get("speed")) * 100, 1)
        if self._chamber_fan_key and self._chamber_fan_key in obj:
            status.chamber_fan = round(_as_float(obj[self._chamber_fan_key].get("speed")) * 100, 1)
        status.fans = self._build_fans()
        if self._light_key and self._light_key in obj:
            light = obj[self._light_key]
            status.light_on = _as_float(light.get("value", light.get("white", 0))) > 0

        position = toolhead.get("position") or gcode_move.get("gcode_position") or []
        if isinstance(position, (list, tuple)) and len(position) >= 3:
            status.position = {
                "x": round(_as_float(position[0]), 2),
                "y": round(_as_float(position[1]), 2),
                "z": round(_as_float(position[2]), 3),
            }

        status.spools = self._parse_cfs()
        status.raw = {"print_stats": print_stats, "webhooks": webhooks}
        return status

    def _build_fans(self) -> list[FanState]:
        """Detaille chaque ventilateur present, avec son regime s'il est mesure."""
        fans: list[FanState] = []

        def add(object_name: str | None, label: str, control_key: str | None) -> None:
            if not object_name or object_name not in self._objects:
                return
            payload = self._objects[object_name]
            if not isinstance(payload, dict):
                return
            rpm = payload.get("rpm")
            fans.append(
                FanState(
                    key=control_key,
                    label=label,
                    speed=round(_as_float(payload.get("speed")) * 100, 1),
                    rpm=round(float(rpm)) if isinstance(rpm, (int, float)) else None,
                    controllable=control_key is not None,
                )
            )

        add("fan", "Piece", "part")
        add(self._aux_fan_key, "Auxiliaire", "aux")
        add(self._chamber_fan_key, "Chambre", "chamber")
        for name in self._extra_fans:
            # "heater_fan hotend_fan" -> "Hotend fan"
            label = name.split(" ", 1)[-1].replace("_", " ").strip().capitalize()
            add(name, label or name, None)
        return fans

    def _parse_cfs(self) -> list[SpoolState]:
        """Lit l'objet CFS Creality expose par Moonraker."""
        if not self._cfs_key:
            return []
        return parse_cfs_payload(self._objects.get(self._cfs_key))

    # ------------------------------------------------------------- commandes
    async def _post(self, path: str, **kwargs: Any) -> Any:
        return await self._request("POST", path, **kwargs)

    async def pause(self) -> None:
        await self._post("/printer/print/pause")

    async def resume(self) -> None:
        await self._post("/printer/print/resume")

    async def cancel(self) -> None:
        await self._post("/printer/print/cancel")

    async def start_print(self, remote_name: str) -> None:
        await self._post("/printer/print/start", params={"filename": remote_name})

    async def send_gcode(self, script: str) -> None:
        await self._post("/printer/gcode/script", params={"script": script})

    async def emergency_stop(self) -> None:
        await self._post("/printer/emergency_stop")

    async def set_temperature(self, heater: str, value: float) -> None:
        value = max(0.0, float(value))
        if heater == "extruder":
            await self.send_gcode(f"SET_HEATER_TEMPERATURE HEATER=extruder TARGET={value:.0f}")
        elif heater == "bed":
            await self.send_gcode(f"SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET={value:.0f}")
        elif heater == "chamber":
            name = (self._chamber_key or "").split(" ", 1)[-1] or "chamber_heater"
            await self.send_gcode(f"SET_HEATER_TEMPERATURE HEATER={name} TARGET={value:.0f}")
        else:
            raise PrinterError(f"Chauffe inconnue: {heater}")

    async def set_fan(self, fan: str, speed: float) -> None:
        value = max(0.0, min(100.0, float(speed)))
        if fan == "part":
            await self.send_gcode(f"M106 S{int(value * 255 / 100)}")
            return
        key = self._aux_fan_key if fan == "aux" else self._chamber_fan_key
        if not key:
            raise PrinterError(f"Ventilateur '{fan}' absent de cette machine")
        await self.send_gcode(f"SET_FAN_SPEED FAN={key.split(' ', 1)[-1]} SPEED={value / 100:.2f}")

    async def set_light(self, on: bool) -> None:
        if not self._light_key:
            raise PrinterError("Aucun eclairage pilotable detecte")
        pin = self._light_key.split(" ", 1)[-1]
        if self._light_key.startswith("led"):
            level = 1.0 if on else 0.0
            await self.send_gcode(f"SET_LED LED={pin} WHITE={level:.1f}")
        else:
            await self.send_gcode(f"SET_PIN PIN={pin} VALUE={1 if on else 0}")

    async def home(self, axes: str = "XYZ") -> None:
        axes = "".join(a for a in axes.upper() if a in "XYZ")
        await self.send_gcode(f"G28 {' '.join(axes)}".strip() if axes else "G28")

    async def move(self, axis: str, distance: float, speed: float = 3000) -> None:
        axis = axis.upper()
        if axis not in ("X", "Y", "Z"):
            raise PrinterError(f"Axe invalide: {axis}")
        await self.send_gcode(
            f"SAVE_GCODE_STATE NAME=allbudy_move\nG91\nG1 {axis}{distance:.3f} F{speed:.0f}\n"
            "RESTORE_GCODE_STATE NAME=allbudy_move"
        )

    async def extrude(self, distance: float, speed: float = 300) -> None:
        await self.send_gcode(
            f"SAVE_GCODE_STATE NAME=allbudy_ext\nM83\nG1 E{distance:.2f} F{speed:.0f}\n"
            "RESTORE_GCODE_STATE NAME=allbudy_ext"
        )

    async def set_speed_factor(self, percent: float) -> None:
        await self.send_gcode(f"M220 S{max(10, min(300, int(percent)))}")

    # -------------------------------------------------------------- fichiers
    async def list_files(self) -> list[RemoteFile]:
        data = await self._request(
            "GET", "/server/files/list", params={"root": self.config.upload_root}
        )
        result = (data or {}).get("result") if isinstance(data, dict) else None
        files: list[RemoteFile] = []
        for item in result or []:
            files.append(
                RemoteFile(
                    name=item.get("path") or item.get("filename") or "",
                    size=int(item.get("size") or 0),
                    modified=item.get("modified"),
                    path=item.get("path"),
                )
            )
        return files

    async def upload(self, local_path: Path, remote_name: str, start: bool = False) -> str:
        path = Path(local_path)
        if not path.exists():
            raise PrinterError(f"Fichier introuvable: {path}")
        with path.open("rb") as handle:
            files = {"file": (remote_name, handle, "application/octet-stream")}
            data = {"root": self.config.upload_root, "print": "true" if start else "false"}
            await self._request("POST", "/server/files/upload", files=files, data=data)
        return remote_name

    async def delete_file(self, remote_name: str) -> None:
        await self._request("DELETE", f"/server/files/{self.config.upload_root}/{remote_name}")

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "gcode": True,
            "temperature": True,
            "fans": True,
            "light": self._light_key is not None,
            "motion": True,
            "upload": True,
            "files": True,
            "cfs": self._cfs_key is not None,
            "emergency_stop": True,
            "push_updates": self._ws_ok,
        }
