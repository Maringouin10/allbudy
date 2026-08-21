"""Bibliotheque locale de fichiers tranches."""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
import unicodedata
from pathlib import Path
from typing import Any, BinaryIO

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import GcodeFile
from .gcode_meta import extract_gcode_metadata, guess_image_extension
from .threemf import extract_3mf_metadata

log = logging.getLogger("allbudy.files")

GCODE_SUFFIXES = (".gcode", ".gco", ".g")
THREEMF_SUFFIXES = (".3mf",)
ALLOWED_SUFFIXES = GCODE_SUFFIXES + THREEMF_SUFFIXES

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_CHUNK = 1 << 20


def sanitize_filename(name: str) -> str:
    """Nom sur sans accent ni separateur: les firmwares Creality y sont sensibles."""
    name = Path(name.replace("\\", "/")).name
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = _UNSAFE.sub("_", name).strip("._") or "fichier"
    return name[:180]


def file_kind(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in THREEMF_SUFFIXES:
        return "3mf"
    if suffix in GCODE_SUFFIXES:
        return "gcode"
    raise ValueError(f"Extension non supportee: {suffix or '(aucune)'}")


def local_path(stored_name: str) -> Path:
    return get_settings().files_dir / stored_name


def thumbnail_path(name: str) -> Path:
    return get_settings().thumbs_dir / name


def hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def analyze(path: Path, kind: str) -> tuple[dict[str, Any], bytes | None]:
    """Extrait metadonnees et miniature, sans jamais faire echouer l'import."""
    try:
        if kind == "3mf":
            meta, thumbnail = extract_3mf_metadata(path)
        else:
            meta, thumbnail = extract_gcode_metadata(path)
        return meta.to_dict(), thumbnail
    except (ValueError, OSError) as exc:
        log.warning("Analyse impossible pour %s: %s", path.name, exc)
        return {"printable": kind == "gcode", "analysis_error": str(exc)}, None


def write_stream(source: BinaryIO, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        shutil.copyfileobj(source, handle, _CHUNK)


async def register_file(
    session: AsyncSession,
    temp_path: Path,
    original_name: str,
    *,
    source: str = "upload",
    remote_id: int | None = None,
    remote_path: str | None = None,
) -> tuple[GcodeFile, bool]:
    """Integre un fichier temporaire dans la bibliotheque.

    Retourne (fichier, cree). Un contenu deja present (meme sha256) n'est pas
    duplique: on renvoie l'entree existante.
    """
    settings = get_settings()
    settings.ensure_dirs()

    kind = file_kind(original_name)
    sha, size = hash_file(temp_path)

    existing = (
        await session.execute(select(GcodeFile).where(GcodeFile.sha256 == sha))
    ).scalar_one_or_none()
    if existing is not None:
        temp_path.unlink(missing_ok=True)
        return existing, False

    safe_name = sanitize_filename(original_name)
    stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
    stored_name = f"{stem}-{sha[:8]}{suffix}"
    destination = local_path(stored_name)
    shutil.move(str(temp_path), destination)

    meta, thumbnail = analyze(destination, kind)
    thumb_name: str | None = None
    if thumbnail and settings.keep_thumbnails:
        thumb_name = f"{stem}-{sha[:8]}{guess_image_extension(thumbnail)}"
        thumbnail_path(thumb_name).write_bytes(thumbnail)

    record = GcodeFile(
        filename=safe_name,
        stored_name=stored_name,
        size=size,
        sha256=sha,
        kind=kind,
        source=source,
        remote_id=remote_id,
        remote_path=remote_path,
        thumbnail=thumb_name,
        meta=meta,
    )
    session.add(record)
    await session.flush()
    return record, True


async def delete_file(session: AsyncSession, record: GcodeFile) -> None:
    local_path(record.stored_name).unlink(missing_ok=True)
    if record.thumbnail:
        thumbnail_path(record.thumbnail).unlink(missing_ok=True)
    await session.delete(record)


def describe(record: GcodeFile) -> dict[str, Any]:
    meta = record.meta or {}
    return {
        "id": record.id,
        "filename": record.filename,
        "stored_name": record.stored_name,
        "size": record.size,
        "kind": record.kind,
        "source": record.source,
        "sha256": record.sha256,
        "remote_id": record.remote_id,
        "remote_path": record.remote_path,
        "thumbnail": record.thumbnail,
        "printable": bool(meta.get("printable", record.kind == "gcode")),
        "meta": meta,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }
