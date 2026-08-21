"""API des bobines de filament (CFS et bobines externes)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..events import bus
from ..models import Printer, Spool
from ..queueing import scheduler
from ..schemas import MessageResponse, SpoolCreate, SpoolOut, SpoolUpdate
from ..security import current_user

router = APIRouter(prefix="/api/spools", tags=["spools"], dependencies=[Depends(current_user)])


@router.get("", response_model=list[SpoolOut])
async def list_spools(
    printer_id: int | None = None,
    stock: bool = False,
    session: AsyncSession = Depends(get_session),
) -> list[Spool]:
    query = select(Spool)
    if stock:
        query = query.where(Spool.printer_id.is_(None))
    elif printer_id is not None:
        query = query.where(Spool.printer_id == printer_id)
    query = query.order_by(Spool.printer_id, Spool.unit, Spool.slot)
    return list((await session.execute(query)).scalars())


@router.get("/inventory")
async def inventory(session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    """Vue par imprimante, pour l'ecran filaments."""
    printers = list(
        (await session.execute(select(Printer).order_by(Printer.sort_order, Printer.id)))
        .scalars()
    )
    spools = list((await session.execute(select(Spool))).scalars())
    by_printer: dict[int | None, list[Spool]] = {}
    for spool in spools:
        by_printer.setdefault(spool.printer_id, []).append(spool)

    def serialize(items: list[Spool]) -> list[dict[str, Any]]:
        return [
            SpoolOut.model_validate(s).model_dump(mode="json")
            for s in sorted(items, key=lambda s: (s.unit, s.slot))
        ]

    result = [
        {
            "printer_id": printer.id,
            "printer": printer.name,
            "has_cfs": printer.has_cfs,
            "spools": serialize(by_printer.get(printer.id, [])),
        }
        for printer in printers
    ]
    result.append(
        {
            "printer_id": None,
            "printer": "Stock (non monte)",
            "has_cfs": False,
            "spools": serialize(by_printer.get(None, [])),
        }
    )
    return result


@router.post("", response_model=SpoolOut, status_code=201)
async def create_spool(payload: SpoolCreate, session: AsyncSession = Depends(get_session)) -> Spool:
    if payload.printer_id is not None and await session.get(Printer, payload.printer_id) is None:
        raise HTTPException(status_code=404, detail="Imprimante introuvable")
    spool = Spool(**payload.model_dump())
    if spool.remaining_g is None and spool.total_g:
        spool.remaining_g = spool.total_g
    session.add(spool)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="Cet emplacement est deja occupe sur cette imprimante"
        ) from exc
    await session.refresh(spool)
    bus.publish("spools.updated", {"printer_id": spool.printer_id})
    scheduler.wake()
    return spool


@router.patch("/{spool_id}", response_model=SpoolOut)
async def update_spool(
    spool_id: int, payload: SpoolUpdate, session: AsyncSession = Depends(get_session)
) -> Spool:
    spool = await session.get(Spool, spool_id)
    if spool is None:
        raise HTTPException(status_code=404, detail="Bobine introuvable")
    data = payload.model_dump(exclude_unset=True)
    target = data.get("printer_id")
    if target is not None and await session.get(Printer, target) is None:
        raise HTTPException(status_code=404, detail="Imprimante introuvable")
    for key, value in data.items():
        setattr(spool, key, value)
    # Une modification manuelle reprend la main sur la synchro CFS.
    if data:
        spool.managed = False
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="Cet emplacement est deja occupe sur cette imprimante"
        ) from exc
    await session.refresh(spool)
    bus.publish("spools.updated", {"printer_id": spool.printer_id})
    scheduler.wake()
    return spool


@router.delete("/{spool_id}", response_model=MessageResponse)
async def delete_spool(
    spool_id: int, session: AsyncSession = Depends(get_session)
) -> MessageResponse:
    spool = await session.get(Spool, spool_id)
    if spool is None:
        raise HTTPException(status_code=404, detail="Bobine introuvable")
    printer_id = spool.printer_id
    await session.delete(spool)
    await session.commit()
    bus.publish("spools.updated", {"printer_id": printer_id})
    return MessageResponse(message="Bobine supprimee")
