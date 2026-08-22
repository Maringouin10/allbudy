"""Informations systeme, journal d'evenements et statistiques."""
from __future__ import annotations

import platform
import shutil
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import __version__
from ..config import get_settings
from ..db import get_session
from ..events import bus
from ..models import EventLog, GcodeFile, Job, JobStatus, Printer, Spool
from ..printers.base import STATE_PRINTING
from ..printers.manager import manager
from ..queueing import scheduler
from ..schemas import MessageResponse
from ..security import current_user

router = APIRouter(prefix="/api/system", tags=["system"])

_STARTED_AT = time.time()


@router.get("/info")
async def info() -> dict[str, Any]:
    """Accessible sans authentification: sert a l'ecran de connexion."""
    settings = get_settings()
    return {
        "name": "AllBudy",
        "version": __version__,
        "auth_enabled": settings.auth_enabled,
        "base_path": settings.base_path,
        "poll_interval": settings.poll_interval,
        "max_upload_mb": settings.max_upload_mb,
    }


@router.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "uptime_s": int(time.time() - _STARTED_AT),
        "printers": len(manager.snapshot()),
        "clients": bus.subscriber_count,
    }


@router.get("/stats", dependencies=[Depends(current_user)])
async def stats(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    settings = get_settings()
    snapshot = manager.snapshot()
    printing = [s for s in snapshot if s["status"]["state"] == STATE_PRINTING]

    job_counts = dict(
        (await session.execute(select(Job.status, func.count(Job.id)).group_by(Job.status))).all()
    )
    since = datetime.now(UTC) - timedelta(days=7)
    completed_week = (
        await session.execute(
            select(func.count(Job.id)).where(
                Job.status == JobStatus.COMPLETED.value, Job.finished_at >= since
            )
        )
    ).scalar_one()

    # Pieces reellement sorties: chaque exemplaire termine compte autant de
    # pieces que le plateau en portait (1 par defaut si le fichier ne le dit pas).
    pieces_week = 0
    recent = (
        await session.execute(
            select(Job.copies_done, GcodeFile.meta)
            .join(GcodeFile, Job.file_id == GcodeFile.id)
            .where(Job.status == JobStatus.COMPLETED.value, Job.finished_at >= since)
        )
    ).all()
    for copies_done, meta in recent:
        per_plate = (meta or {}).get("object_count") or 1
        pieces_week += int(copies_done or 0) * int(per_plate)

    files_count = (await session.execute(select(func.count(GcodeFile.id)))).scalar_one()
    files_bytes = (await session.execute(select(func.sum(GcodeFile.size)))).scalar() or 0
    spools_count = (await session.execute(select(func.count(Spool.id)))).scalar_one()
    printers_count = (await session.execute(select(func.count(Printer.id)))).scalar_one()

    usage = shutil.disk_usage(settings.data_dir)
    return {
        "printers": {
            "total": printers_count,
            "online": sum(1 for s in snapshot if s["connected"]),
            "printing": len(printing),
            # Libres = connectees et pretes a prendre un travail tout de suite.
            "free": sum(1 for s in snapshot if s["connected"] and s["status"]["is_free"]),
        },
        "jobs": {
            "counts": job_counts,
            "queued": job_counts.get(JobStatus.QUEUED.value, 0),
            "completed_7d": completed_week,
            "pieces_7d": pieces_week,
        },
        "library": {"files": files_count, "bytes": int(files_bytes)},
        "spools": spools_count,
        "disk": {"total": usage.total, "used": usage.used, "free": usage.free},
        "dispatcher_enabled": scheduler.enabled,
        "uptime_s": int(time.time() - _STARTED_AT),
        "platform": f"{platform.system()} {platform.machine()}",
    }


@router.get("/events", dependencies=[Depends(current_user)])
async def events(
    limit: int = Query(default=100, ge=1, le=1000),
    level: str | None = None,
    category: str | None = None,
    printer_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    query = select(EventLog).order_by(EventLog.id.desc()).limit(limit)
    if level:
        query = query.where(EventLog.level == level)
    if category:
        query = query.where(EventLog.category == category)
    if printer_id is not None:
        query = query.where(EventLog.printer_id == printer_id)
    return [
        {
            "id": row.id,
            "ts": row.ts.isoformat() if row.ts else None,
            "level": row.level,
            "category": row.category,
            "message": row.message,
            "printer_id": row.printer_id,
            "job_id": row.job_id,
            "data": row.data,
        }
        for row in (await session.execute(query)).scalars()
    ]


@router.delete("/events", response_model=MessageResponse, dependencies=[Depends(current_user)])
async def clear_events(
    keep_days: int = Query(default=0, ge=0, le=365), session: AsyncSession = Depends(get_session)
) -> MessageResponse:
    query = delete(EventLog)
    if keep_days:
        query = query.where(EventLog.ts < datetime.now(UTC) - timedelta(days=keep_days))
    result = await session.execute(query)
    await session.commit()
    return MessageResponse(message=f"{result.rowcount or 0} evenement(s) supprime(s)")
