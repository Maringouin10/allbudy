"""Decouverte des imprimantes sur le reseau local.

Aucun service cloud n'est interroge: on balaie le sous-reseau local en TCP puis
on identifie chaque machine trouvee via son propre protocole.
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import socket
from dataclasses import asdict, dataclass
from typing import Any

import httpx
import websockets

from ..config import get_settings
from ..models import TransportKind

log = logging.getLogger("allbudy.discovery")


@dataclass(slots=True)
class Candidate:
    host: str
    port: int
    transport: str
    name: str | None = None
    model: str | None = None
    firmware: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def local_ipv4() -> str | None:
    """Adresse IPv4 de l'interface utilisee pour sortir (sans emettre de trafic)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def default_network() -> str | None:
    ip = local_ipv4()
    if not ip:
        return None
    return str(ipaddress.ip_network(f"{ip}/24", strict=False))


async def _port_open(host: str, port: int, timeout: float) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
    except (TimeoutError, OSError):
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True


async def _identify_moonraker(host: str, port: int, timeout: float) -> Candidate | None:
    url = f"http://{host}:{port}/printer/info"
    try:
        async with httpx.AsyncClient(timeout=timeout + 2) as client:
            response = await client.get(url)
            if response.status_code >= 400:
                return None
            result = (response.json() or {}).get("result", {})
    except (httpx.HTTPError, ValueError):
        return None
    return Candidate(
        host=host,
        port=port,
        transport=TransportKind.MOONRAKER.value,
        name=result.get("hostname"),
        model=result.get("hostname"),
        firmware=result.get("software_version"),
    )


async def _identify_creality_lan(host: str, port: int, timeout: float) -> Candidate | None:
    try:
        async with asyncio.timeout(timeout + 4):
            async with websockets.connect(f"ws://{host}:{port}", ping_interval=None) as ws:
                await ws.send(json.dumps({"method": "get", "params": {"reqPrinterPara": 1}}))
                for _ in range(4):
                    raw = await ws.recv()
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", "replace")
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    if isinstance(payload.get("params"), dict):
                        payload = payload["params"]
                    if payload.get("model") or payload.get("hostname"):
                        return Candidate(
                            host=host,
                            port=port,
                            transport=TransportKind.CREALITY_LAN.value,
                            name=payload.get("hostname") or payload.get("model"),
                            model=payload.get("model"),
                            firmware=payload.get("modelVersion") or payload.get("softVersion"),
                        )
    except (TimeoutError, OSError, websockets.WebSocketException):
        return None
    return Candidate(host=host, port=port, transport=TransportKind.CREALITY_LAN.value)


_IDENTIFIERS = {
    7125: _identify_moonraker,
    9999: _identify_creality_lan,
}


async def scan(
    network: str | None = None, ports: list[int] | None = None, timeout: float | None = None
) -> list[Candidate]:
    """Balaie un sous-reseau et retourne les imprimantes identifiees.

    `network` accepte une notation CIDR (ex: 192.168.1.0/24). Par defaut, le
    sous-reseau /24 de l'interface locale.
    """
    settings = get_settings()
    ports = ports or settings.discovery_port_list
    timeout = timeout or settings.discovery_timeout
    cidr = network or default_network()
    if not cidr:
        raise ValueError("Impossible de determiner le sous-reseau local")

    net = ipaddress.ip_network(cidr, strict=False)
    if net.num_addresses > 4096:
        raise ValueError(f"Sous-reseau trop large ({net.num_addresses} adresses), utilisez un /20+")

    semaphore = asyncio.Semaphore(settings.discovery_concurrency)
    found: list[Candidate] = []
    lock = asyncio.Lock()

    async def probe(host: str, port: int) -> None:
        async with semaphore:
            if not await _port_open(host, port, timeout):
                return
            identify = _IDENTIFIERS.get(port)
            candidate = await identify(host, port, timeout) if identify else None
            if candidate is None:
                candidate = Candidate(
                    host=host, port=port, transport=TransportKind.MOONRAKER.value
                )
            async with lock:
                found.append(candidate)

    hosts = [str(ip) for ip in (net.hosts() if net.num_addresses > 2 else net)]
    await asyncio.gather(*(probe(host, port) for host in hosts for port in ports))

    # Une meme machine peut repondre sur 7125 et 9999: Moonraker prime.
    best: dict[str, Candidate] = {}
    for candidate in found:
        current = best.get(candidate.host)
        if current is None or candidate.transport == TransportKind.MOONRAKER.value:
            best[candidate.host] = candidate
    result = sorted(best.values(), key=lambda c: ipaddress.ip_address(c.host))
    log.info("Decouverte %s: %d imprimante(s)", cidr, len(result))
    return result
