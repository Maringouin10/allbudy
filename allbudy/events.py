"""Bus d'evenements en memoire + journalisation persistante."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("allbudy.events")


class EventBus:
    """Diffusion fan-out vers les clients WebSocket connectes.

    Chaque abonne possede sa propre file bornee: un client lent est purge de ses
    plus vieux messages plutot que de bloquer le producteur.
    """

    def __init__(self, queue_size: int = 256) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._queue_size = queue_size
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    def publish(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        message = {"type": event_type, "data": payload or {}}
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - concurrence
                    pass
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:  # pragma: no cover - concurrence
                pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


bus = EventBus()


async def record_event(
    message: str,
    *,
    level: str = "info",
    category: str = "system",
    printer_id: int | None = None,
    job_id: int | None = None,
    data: dict[str, Any] | None = None,
    publish: bool = True,
    session: Any = None,
) -> None:
    """Ecrit une ligne de journal en base et la diffuse aux clients.

    `session` doit etre fournie quand l'appelant a deja une transaction en
    cours: SQLite n'accepte qu'un seul ecrivain, et ouvrir une seconde session
    depuis l'interieur d'une transaction bloquerait les deux.
    """
    from .db import session_scope
    from .models import EventLog

    entry = EventLog(
        message=message,
        level=level,
        category=category,
        printer_id=printer_id,
        job_id=job_id,
        data=data or {},
    )
    try:
        if session is not None:
            session.add(entry)
        else:
            async with session_scope() as scoped:
                scoped.add(entry)
    except Exception:  # pragma: no cover - le journal ne doit jamais casser l'appelant
        log.exception("Impossible d'enregistrer l'evenement: %s", message)

    log.log(logging.WARNING if level in ("warning", "error") else logging.INFO, message)
    if publish:
        bus.publish(
            "event",
            {
                "level": level,
                "category": category,
                "message": message,
                "printer_id": printer_id,
                "job_id": job_id,
                "ts": entry.ts.isoformat() if entry.ts else None,
                "data": data or {},
            },
        )
