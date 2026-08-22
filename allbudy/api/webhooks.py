"""API des webhooks sortants (fin d'impression, plateau refroidi)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..events import record_event
from ..models import Printer, Webhook
from ..schemas import MessageResponse, WebhookCreate, WebhookOut, WebhookUpdate
from ..security import current_user

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"], dependencies=[Depends(current_user)])


async def _get_webhook(session: AsyncSession, webhook_id: int) -> Webhook:
    webhook = await session.get(Webhook, webhook_id)
    if webhook is None:
        raise HTTPException(status_code=404, detail="Webhook introuvable")
    return webhook


async def _check_scope(session: AsyncSession, printer_id: int | None, tag: str | None) -> None:
    if printer_id is not None and tag:
        raise HTTPException(
            status_code=400, detail="Choisissez une imprimante ou une etiquette, pas les deux"
        )
    if printer_id is not None and await session.get(Printer, printer_id) is None:
        raise HTTPException(status_code=404, detail="Imprimante introuvable")


@router.get("", response_model=list[WebhookOut])
async def list_webhooks(session: AsyncSession = Depends(get_session)) -> list[Webhook]:
    rows = (await session.execute(select(Webhook).order_by(Webhook.id))).scalars()
    return list(rows)


@router.post("", response_model=WebhookOut, status_code=201)
async def create_webhook(
    payload: WebhookCreate, session: AsyncSession = Depends(get_session)
) -> Webhook:
    await _check_scope(session, payload.printer_id, payload.tag)
    data = payload.model_dump(exclude={"event"})
    data["event"] = payload.event.value
    webhook = Webhook(**data)
    session.add(webhook)
    await session.commit()
    await session.refresh(webhook)
    await record_event(f"Webhook ajoute: {webhook.name or webhook.url}", category="system")
    return webhook


@router.patch("/{webhook_id}", response_model=WebhookOut)
async def update_webhook(
    webhook_id: int, payload: WebhookUpdate, session: AsyncSession = Depends(get_session)
) -> Webhook:
    webhook = await _get_webhook(session, webhook_id)
    data = payload.model_dump(exclude_unset=True)
    printer_id = data.get("printer_id", webhook.printer_id)
    tag = data.get("tag", webhook.tag)
    await _check_scope(session, printer_id, tag)
    if "event" in data:
        data["event"] = data["event"].value
    for key, value in data.items():
        setattr(webhook, key, value)
    await session.commit()
    await session.refresh(webhook)
    return webhook


@router.delete("/{webhook_id}", response_model=MessageResponse)
async def delete_webhook(
    webhook_id: int, session: AsyncSession = Depends(get_session)
) -> MessageResponse:
    webhook = await _get_webhook(session, webhook_id)
    await session.delete(webhook)
    await session.commit()
    return MessageResponse(message="Webhook supprime")
