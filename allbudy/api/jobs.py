"""API de la file d'attente."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..events import bus, record_event
from ..models import ACTIVE_JOB_STATUSES, GcodeFile, Job, JobStatus, Printer
from ..printers.base import PrinterError
from ..printers.manager import manager
from ..queueing import scheduler
from ..schemas import JobCreate, JobOut, JobUpdate, MessageResponse, QueueReorder
from ..security import current_user

router = APIRouter(prefix="/api/jobs", tags=["jobs"], dependencies=[Depends(current_user)])


async def _get_job(session: AsyncSession, job_id: int) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Travail introuvable")
    return job


@router.get("", response_model=list[JobOut])
async def list_jobs(
    status: str | None = None,
    printer_id: int | None = None,
    active: bool = False,
    limit: int = Query(default=200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> list[Job]:
    query = select(Job)
    if status:
        query = query.where(Job.status == status)
    if active:
        query = query.where(Job.status.in_([s.value for s in ACTIVE_JOB_STATUSES]))
    if printer_id is not None:
        query = query.where(Job.printer_id == printer_id)
    query = query.order_by(Job.priority.desc(), Job.position.asc(), Job.id.asc()).limit(limit)
    return list((await session.execute(query)).scalars())


@router.post("", response_model=JobOut, status_code=201)
async def create_job(payload: JobCreate, session: AsyncSession = Depends(get_session)) -> Job:
    file = await session.get(GcodeFile, payload.file_id)
    if file is None:
        raise HTTPException(status_code=404, detail="Fichier introuvable")
    if not (file.meta or {}).get("printable", file.kind == "gcode"):
        raise HTTPException(
            status_code=400,
            detail="Ce 3MF n'est pas tranche: tranchez-le avant de le mettre en file",
        )
    if payload.printer_id is not None and await session.get(Printer, payload.printer_id) is None:
        raise HTTPException(status_code=404, detail="Imprimante introuvable")

    max_position = (await session.execute(select(func.max(Job.position)))).scalar() or 0
    job = Job(
        name=payload.name or file.filename,
        file_id=file.id,
        priority=payload.priority,
        position=max_position + 1,
        copies=payload.copies,
        required_material=payload.required_material,
        required_color=payload.required_color,
        required_filaments=[
            entry.model_dump() if entry else None for entry in payload.required_filaments
        ],
        color_tolerance=payload.color_tolerance,
        required_nozzle=payload.required_nozzle,
        required_tags=payload.required_tags,
        # Un travail epingle sur une machine n'est propose qu'a celle-ci.
        allowed_printers=[payload.printer_id]
        if payload.printer_id is not None
        else payload.allowed_printers,
        auto_start=payload.auto_start,
        bed_leveling=payload.bed_leveling,
        scheduled_at=payload.scheduled_at,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    await record_event(
        f"Travail #{job.id} '{job.name}' ajoute a la file", category="job", job_id=job.id
    )
    bus.publish("job.created", {"job_id": job.id})
    scheduler.wake()
    return job


@router.get("/stats")
async def queue_stats(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    rows = (
        await session.execute(select(Job.status, func.count(Job.id)).group_by(Job.status))
    ).all()
    counts = dict(rows)
    return {
        "counts": counts,
        "queued": counts.get(JobStatus.QUEUED.value, 0),
        "active": sum(counts.get(s.value, 0) for s in ACTIVE_JOB_STATUSES),
        "dispatcher_enabled": scheduler.enabled,
    }


@router.post("/reorder", response_model=MessageResponse)
async def reorder(
    payload: QueueReorder, session: AsyncSession = Depends(get_session)
) -> MessageResponse:
    """Reordonne la file: le premier id de la liste passe en tete."""
    jobs = {
        job.id: job
        for job in (
            await session.execute(select(Job).where(Job.id.in_(payload.job_ids)))
        ).scalars()
    }
    missing = [job_id for job_id in payload.job_ids if job_id not in jobs]
    if missing:
        raise HTTPException(status_code=404, detail=f"Travaux introuvables: {missing}")
    for position, job_id in enumerate(payload.job_ids):
        jobs[job_id].position = position
    await session.commit()
    bus.publish("queue.reordered", {"job_ids": payload.job_ids})
    scheduler.wake()
    return MessageResponse(message="File reordonnee")


@router.post("/dispatcher", response_model=MessageResponse)
async def toggle_dispatcher(enabled: bool) -> MessageResponse:
    """Active/suspend l'attribution automatique (les impressions en cours continuent)."""
    scheduler.enabled = enabled
    await record_event(
        "Attribution automatique " + ("activee" if enabled else "suspendue"), category="queue"
    )
    if enabled:
        scheduler.wake()
    return MessageResponse(message="Dispatcher " + ("actif" if enabled else "suspendu"))


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: int, session: AsyncSession = Depends(get_session)) -> Job:
    return await _get_job(session, job_id)


@router.patch("/{job_id}", response_model=JobOut)
async def update_job(
    job_id: int, payload: JobUpdate, session: AsyncSession = Depends(get_session)
) -> Job:
    job = await _get_job(session, job_id)
    data = payload.model_dump(exclude_unset=True)
    if "status" in data:
        new_status = data.pop("status")
        data["status"] = new_status.value if hasattr(new_status, "value") else new_status
    if job.status in (JobStatus.PRINTING.value, JobStatus.SENDING.value) and set(data) - {
        "name",
        "priority",
        "position",
    }:
        raise HTTPException(
            status_code=409,
            detail="Travail en cours: seuls le nom et la position restent modifiables",
        )
    for key, value in data.items():
        setattr(job, key, value)
    await session.commit()
    await session.refresh(job)
    bus.publish("job.updated", {"job_id": job.id, "status": job.status})
    scheduler.wake()
    return job


@router.get("/{job_id}/match")
async def explain_match(job_id: int, session: AsyncSession = Depends(get_session)):
    """Explique, machine par machine, pourquoi un travail attend."""
    await _get_job(session, job_id)
    return await scheduler.explain(job_id)


@router.post("/{job_id}/cancel", response_model=MessageResponse)
async def cancel_job(
    job_id: int, session: AsyncSession = Depends(get_session)
) -> MessageResponse:
    job = await _get_job(session, job_id)
    if job.status in (JobStatus.COMPLETED.value, JobStatus.CANCELLED.value):
        raise HTTPException(status_code=409, detail="Ce travail est deja termine")

    stopped_printer = False
    if job.status in (JobStatus.PRINTING.value, JobStatus.PAUSED.value) and job.printer_id:
        try:
            await manager.command(job.printer_id, lambda t: t.cancel())
            stopped_printer = True
        except PrinterError as exc:
            raise HTTPException(
                status_code=409, detail=f"Impossible d'arreter l'imprimante: {exc}"
            ) from exc

    job.status = JobStatus.CANCELLED.value
    job.finished_at = datetime.now(UTC)
    await session.commit()
    await record_event(
        f"Travail #{job.id} '{job.name}' annule",
        level="warning",
        category="job",
        job_id=job.id,
        printer_id=job.printer_id,
    )
    bus.publish("job.updated", {"job_id": job.id, "status": job.status})
    scheduler.wake()
    return MessageResponse(
        message="Travail annule" + (" et imprimante arretee" if stopped_printer else "")
    )


@router.post("/{job_id}/requeue", response_model=MessageResponse)
async def requeue_job(
    job_id: int, session: AsyncSession = Depends(get_session)
) -> MessageResponse:
    """Remet en file un travail termine, annule ou en echec."""
    job = await _get_job(session, job_id)
    if job.status in (JobStatus.PRINTING.value, JobStatus.SENDING.value):
        raise HTTPException(status_code=409, detail="Ce travail est en cours")
    job.status = JobStatus.QUEUED.value
    job.printer_id = None
    job.progress = 0.0
    job.copies_done = 0
    job.attempts = 0
    job.error = None
    job.assigned_at = job.started_at = job.finished_at = None
    await session.commit()
    bus.publish("job.updated", {"job_id": job.id, "status": job.status})
    scheduler.wake()
    return MessageResponse(message=f"Travail #{job.id} remis en file")


@router.delete("/{job_id}", response_model=MessageResponse)
async def delete_job(
    job_id: int, session: AsyncSession = Depends(get_session)
) -> MessageResponse:
    job = await _get_job(session, job_id)
    if job.status in (JobStatus.PRINTING.value, JobStatus.SENDING.value):
        raise HTTPException(
            status_code=409, detail="Annulez le travail avant de le supprimer"
        )
    await session.delete(job)
    await session.commit()
    bus.publish("job.removed", {"job_id": job_id})
    return MessageResponse(message=f"Travail #{job_id} supprime")
