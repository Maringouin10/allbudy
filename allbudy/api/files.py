"""API de la bibliotheque de fichiers tranches."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..events import bus, record_event
from ..files.store import (
    analyze,
    delete_file,
    describe,
    file_kind,
    guess_image_extension,
    local_path,
    register_file,
    thumbnail_path,
)
from ..models import ACTIVE_JOB_STATUSES, GcodeFile, Job
from ..schemas import MessageResponse
from ..security import current_user

router = APIRouter(prefix="/api/files", tags=["files"], dependencies=[Depends(current_user)])

_CHUNK = 1 << 20


@router.get("")
async def list_files(
    search: str | None = None,
    kind: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    query = select(GcodeFile)
    count_query = select(func.count(GcodeFile.id))
    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(GcodeFile.filename.ilike(pattern))
        count_query = count_query.where(GcodeFile.filename.ilike(pattern))
    if kind:
        query = query.where(GcodeFile.kind == kind)
        count_query = count_query.where(GcodeFile.kind == kind)

    total = (await session.execute(count_query)).scalar_one()
    rows = (
        await session.execute(
            query.order_by(GcodeFile.created_at.desc(), GcodeFile.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars()
    return {"total": total, "items": [describe(row) for row in rows]}


@router.post("", status_code=201)
async def upload_file(
    file: UploadFile = File(...), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    settings = get_settings()
    if not file.filename:
        raise HTTPException(status_code=400, detail="Nom de fichier manquant")
    try:
        file_kind(file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc

    max_bytes = settings.max_upload_mb * 1024 * 1024
    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        temp_path = Path(handle.name)
        written = 0
        while chunk := await file.read(_CHUNK):
            written += len(chunk)
            if written > max_bytes:
                handle.close()
                temp_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"Fichier trop volumineux (max {settings.max_upload_mb} Mo)",
                )
            handle.write(chunk)

    try:
        record, created = await register_file(session, temp_path, file.filename)
        await session.commit()
    except ValueError as exc:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    finally:
        temp_path.unlink(missing_ok=True)

    if created:
        await record_event(f"Fichier importe: {record.filename}", category="file")
        bus.publish("file.added", {"file_id": record.id})
    return {"created": created, "file": describe(record)}


@router.get("/{file_id}")
async def get_file(file_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    record = await session.get(GcodeFile, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Fichier introuvable")
    return describe(record)


@router.get("/{file_id}/download")
async def download_file(file_id: int, session: AsyncSession = Depends(get_session)) -> FileResponse:
    record = await session.get(GcodeFile, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Fichier introuvable")
    path = local_path(record.stored_name)
    if not path.exists():
        raise HTTPException(status_code=410, detail="Fichier absent du disque")
    return FileResponse(path, filename=record.filename, media_type="application/octet-stream")


@router.get("/{file_id}/thumbnail")
async def get_thumbnail(file_id: int, session: AsyncSession = Depends(get_session)) -> FileResponse:
    record = await session.get(GcodeFile, file_id)
    if record is None or not record.thumbnail:
        raise HTTPException(status_code=404, detail="Aucune miniature")
    path = thumbnail_path(record.thumbnail)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Aucune miniature")
    media = "image/jpeg" if path.suffix in (".jpg", ".jpeg") else "image/png"
    return FileResponse(path, media_type=media, headers={"Cache-Control": "max-age=86400"})


@router.post("/{file_id}/reanalyze")
async def reanalyze(file_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Relit les metadonnees et la miniature (utile apres mise a jour d'AllBudy)."""
    record = await session.get(GcodeFile, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Fichier introuvable")
    path = local_path(record.stored_name)
    if not path.exists():
        raise HTTPException(status_code=410, detail="Fichier absent du disque")

    meta, thumbnail = analyze(path, record.kind)
    record.meta = meta
    if thumbnail and get_settings().keep_thumbnails:
        name = f"{Path(record.stored_name).stem}{guess_image_extension(thumbnail)}"
        thumbnail_path(name).write_bytes(thumbnail)
        record.thumbnail = name
    await session.commit()
    return describe(record)


@router.delete("/{file_id}", response_model=MessageResponse)
async def remove_file(
    file_id: int, session: AsyncSession = Depends(get_session)
) -> MessageResponse:
    record = await session.get(GcodeFile, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Fichier introuvable")
    active = (
        await session.execute(
            select(Job.id).where(
                Job.file_id == file_id, Job.status.in_([s.value for s in ACTIVE_JOB_STATUSES])
            )
        )
    ).first()
    if active:
        raise HTTPException(
            status_code=409, detail="Ce fichier est utilise par un travail en cours"
        )
    name = record.filename
    await delete_file(session, record)
    await session.commit()
    await record_event(f"Fichier supprime: {name}", category="file")
    bus.publish("file.removed", {"file_id": file_id})
    return MessageResponse(message=f"{name} supprime")


@router.get("/{file_id}/preview")
async def preview_gcode(
    file_id: int,
    lines: int = Query(default=120, ge=10, le=2000),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Renvoie l'entete du fichier: pratique pour verifier un tranchage."""
    record = await session.get(GcodeFile, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Fichier introuvable")
    if record.kind != "gcode":
        raise HTTPException(status_code=400, detail="Apercu disponible seulement pour le G-code")
    path = local_path(record.stored_name)
    if not path.exists():
        raise HTTPException(status_code=410, detail="Fichier absent du disque")

    head: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for index, line in enumerate(handle):
            if index >= lines:
                break
            head.append(line.rstrip("\n"))
    return {"filename": record.filename, "lines": head}
