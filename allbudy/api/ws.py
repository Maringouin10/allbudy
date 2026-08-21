"""WebSocket temps reel vers le navigateur.

Le serveur pousse l'etat des imprimantes et les evenements; le client n'a
qu'a ecouter. Un ping applicatif regulier permet de detecter une coupure
meme quand rien ne change dans le parc.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..db import get_sessionmaker
from ..events import bus
from ..printers.manager import manager
from ..security import websocket_user_ok

log = logging.getLogger("allbudy.ws")
router = APIRouter()

PING_INTERVAL = 25.0


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token") or websocket.cookies.get("allbudy_token")
    async with get_sessionmaker()() as session:
        if not await websocket_user_ok(token, session):
            await websocket.close(code=4401, reason="Authentification requise")
            return

    await websocket.accept()
    queue = await bus.subscribe()
    try:
        # Etat complet a la connexion: le client n'a pas a interroger l'API.
        await websocket.send_json({"type": "snapshot", "data": {"printers": manager.snapshot()}})
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=PING_INTERVAL)
            except TimeoutError:
                await websocket.send_json({"type": "ping", "data": {}})
                continue
            await websocket.send_json(message)
    except WebSocketDisconnect:
        pass
    except (RuntimeError, ConnectionError) as exc:  # client parti en cours d'envoi
        log.debug("WebSocket ferme: %s", exc)
    finally:
        await bus.unsubscribe(queue)
        with contextlib.suppress(RuntimeError):
            await websocket.close()
