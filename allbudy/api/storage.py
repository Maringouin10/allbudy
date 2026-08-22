"""API des depots distants FTP / FTPS / SFTP."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..events import bus, record_event
from ..files.remotes import (
    RemoteEntry,
    RemoteError,
    import_entry,
    list_remote,
    sync_storage,
    test_connection,
    upload_remote,
)
from ..files.store import local_path
from ..models import GcodeFile, RemoteStorage, StorageKind
from ..schemas import MessageResponse, StorageCreate, StorageOut, StorageUpdate
from ..security import current_user, encrypt_secret

router = APIRouter(prefix="/api/storage", tags=["storage"], dependencies=[Depends(current_user)])

DEFAULT_PORTS = {
    StorageKind.FTP.value: 21,
    StorageKind.FTPS.value: 21,
    StorageKind.SFTP.value: 22,
}


def _serialize(storage: RemoteStorage) -> StorageOut:
    out = StorageOut.model_validate(storage)
    out.has_password = bool(storage.secret)
    return out


async def _get_storage(session: AsyncSession, storage_id: int) -> RemoteStorage:
    storage = await session.get(RemoteStorage, storage_id)
    if storage is None:
        raise HTTPException(status_code=404, detail="Depot introuvable")
    return storage


@router.get("", response_model=list[StorageOut])
async def list_storages(session: AsyncSession = Depends(get_session)) -> list[StorageOut]:
    rows = (await session.execute(select(RemoteStorage).order_by(RemoteStorage.id))).scalars()
    return [_serialize(row) for row in rows]


@router.post("", response_model=StorageOut, status_code=201)
async def create_storage(
    payload: StorageCreate, session: AsyncSession = Depends(get_session)
) -> StorageOut:
    data = payload.model_dump(exclude={"password"})
    data["kind"] = payload.kind.value
    data["port"] = payload.port or DEFAULT_PORTS.get(data["kind"], 22)
    storage = RemoteStorage(**data, secret=encrypt_secret(payload.password))
    session.add(storage)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Ce nom de depot existe deja") from exc
    await session.refresh(storage)
    await record_event(f"Depot distant ajoute: {storage.name}", category="storage")
    return _serialize(storage)


@router.patch("/{storage_id}", response_model=StorageOut)
async def update_storage(
    storage_id: int, payload: StorageUpdate, session: AsyncSession = Depends(get_session)
) -> StorageOut:
    storage = await _get_storage(session, storage_id)
    data = payload.model_dump(exclude_unset=True)
    password = data.pop("password", None)
    if password is not None:
        # Une chaine vide efface le mot de passe enregistre.
        storage.secret = encrypt_secret(password) if password else None
    if isinstance(data.get("kind"), StorageKind):
        data["kind"] = data["kind"].value
    for key, value in data.items():
        setattr(storage, key, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Ce nom de depot existe deja") from exc
    await session.refresh(storage)
    return _serialize(storage)


@router.delete("/{storage_id}", response_model=MessageResponse)
async def delete_storage(
    storage_id: int, session: AsyncSession = Depends(get_session)
) -> MessageResponse:
    storage = await _get_storage(session, storage_id)
    name = storage.name
    await session.delete(storage)
    await session.commit()
    return MessageResponse(message=f"Depot {name} supprime")


@router.post("/{storage_id}/test", response_model=MessageResponse)
async def test_storage(
    storage_id: int, session: AsyncSession = Depends(get_session)
) -> MessageResponse:
    storage = await _get_storage(session, storage_id)
    try:
        count = await test_connection(storage)
    except RemoteError as exc:
        storage.last_error = str(exc)[:1000]
        await session.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    storage.last_error = None
    await session.commit()
    return MessageResponse(message=f"Connexion reussie, {count} fichier(s) imprimable(s) visibles")


@router.get("/{storage_id}/browse")
async def browse_storage(
    storage_id: int, path: str | None = None, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    storage = await _get_storage(session, storage_id)
    try:
        entries = await list_remote(storage, path)
    except RemoteError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    known = {
        row
        for (row,) in (
            await session.execute(
                select(GcodeFile.remote_path).where(GcodeFile.remote_id == storage.id)
            )
        ).all()
        if row
    }
    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    return {
        "path": path or storage.remote_path,
        "entries": [{**e.to_dict(), "imported": e.path in known} for e in entries],
    }


@router.post("/{storage_id}/sync")
async def sync(storage_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Importe dans la bibliotheque tous les fichiers encore absents."""
    storage = await _get_storage(session, storage_id)
    try:
        result = await sync_storage(session, storage)
    except RemoteError as exc:
        storage.last_error = str(exc)[:1000]
        await session.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await session.commit()
    if result["imported"]:
        await record_event(
            f"Depot {storage.name}: {len(result['imported'])} fichier(s) importe(s)",
            category="storage",
        )
        bus.publish("file.added", {"count": len(result["imported"])})
    return result


@router.post("/{storage_id}/import", response_model=MessageResponse)
async def import_one(
    storage_id: int,
    path: str = Body(..., embed=True),
    session: AsyncSession = Depends(get_session),
) -> MessageResponse:
    storage = await _get_storage(session, storage_id)
    entry = RemoteEntry(name=path.rsplit("/", 1)[-1], path=path)
    try:
        record, created = await import_entry(session, storage, entry)
    except (RemoteError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await session.commit()
    if record is None:
        raise HTTPException(status_code=502, detail="Import impossible")
    bus.publish("file.added", {"file_id": record.id})
    return MessageResponse(
        message=f"{record.filename} " + ("importe" if created else "deja present"),
        data={"file_id": record.id, "created": created},
    )


@router.post("/{storage_id}/push", response_model=MessageResponse)
async def push_file(
    storage_id: int,
    file_id: int = Body(..., embed=True),
    session: AsyncSession = Depends(get_session),
) -> MessageResponse:
    """Envoie un fichier de la bibliotheque vers le depot (sauvegarde/partage)."""
    storage = await _get_storage(session, storage_id)
    record = await session.get(GcodeFile, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Fichier introuvable")
    path = local_path(record.stored_name)
    if not path.exists():
        raise HTTPException(status_code=410, detail="Fichier absent du disque")
    target = f"{(storage.remote_path or '/').rstrip('/')}/{record.filename}"
    try:
        await upload_remote(storage, path, target)
    except RemoteError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await record_event(
        f"{record.filename} envoye vers le depot {storage.name}", category="storage"
    )
    return MessageResponse(message=f"{record.filename} envoye vers {target}")
