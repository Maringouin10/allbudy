"""Notifications HTTP sortantes: fin d'impression, plateau refroidi.

Un webhook en echec (URL injoignable, 4xx/5xx) ne doit jamais faire echouer
l'evenement qui le declenche (fin de travail, cycle de poll d'une
imprimante): l'erreur est notee sur le webhook (`last_error`) et journalisee,
rien de plus.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Job, Printer, Webhook, WebhookEvent

log = logging.getLogger("allbudy.webhooks")

TIMEOUT = 10.0


async def matching_webhooks(
    session: AsyncSession, event: WebhookEvent, printer: Printer
) -> list[Webhook]:
    """Webhooks actifs pour cet evenement dont la portee couvre cette machine."""
    rows = (
        await session.execute(
            select(Webhook).where(Webhook.event == event.value, Webhook.enabled.is_(True))
        )
    ).scalars()
    printer_tags = {str(t).lower() for t in (printer.tags or [])}
    matches = []
    for webhook in rows:
        if webhook.printer_id is not None and webhook.printer_id != printer.id:
            continue
        if webhook.tag and webhook.tag.lower() not in printer_tags:
            continue
        matches.append(webhook)
    return matches


async def fire(session: AsyncSession, webhook: Webhook, payload: dict[str, Any]) -> None:
    """Envoie le payload et note le resultat sur le webhook (sans lever)."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(webhook.url, json=payload)
            response.raise_for_status()
        webhook.last_error = None
    except Exception as exc:  # noqa: BLE001 - un webhook en echec ne doit jamais se propager
        webhook.last_error = str(exc)[:500]
        log.warning("Webhook #%s (%s) en echec: %s", webhook.id, webhook.url, exc)
    webhook.last_fired_at = datetime.now(UTC)


async def notify_print_finished(session: AsyncSession, printer: Printer, job: Job) -> None:
    webhooks = await matching_webhooks(session, WebhookEvent.PRINT_FINISHED, printer)
    if not webhooks:
        return
    payload = {
        "event": WebhookEvent.PRINT_FINISHED.value,
        "printer_id": printer.id,
        "printer_name": printer.name,
        "job_id": job.id,
        "job_name": job.name,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    for webhook in webhooks:
        await fire(session, webhook, payload)


def bed_cold_payload(printer: Printer, bed_temp: float) -> dict[str, Any]:
    return {
        "event": WebhookEvent.BED_COLD.value,
        "printer_id": printer.id,
        "printer_name": printer.name,
        "bed_temp": bed_temp,
        "timestamp": datetime.now(UTC).isoformat(),
    }
