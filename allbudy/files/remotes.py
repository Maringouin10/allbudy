"""Depots distants FTP / FTPS / SFTP.

Les bibliotheques utilisees (ftplib, paramiko) sont bloquantes: chaque appel
reseau est deporte dans un thread pour ne pas figer la boucle asyncio.
"""
from __future__ import annotations

import asyncio
import ftplib
import logging
import stat
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import GcodeFile, RemoteStorage, StorageKind
from ..security import decrypt_secret
from .store import ALLOWED_SUFFIXES, register_file

log = logging.getLogger("allbudy.remotes")

CONNECT_TIMEOUT = 15


class RemoteError(RuntimeError):
    """Echec de dialogue avec un depot distant."""


@dataclass(slots=True)
class RemoteEntry:
    name: str
    path: str
    size: int = 0
    modified: float | None = None
    is_dir: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "size": self.size,
            "modified": datetime.fromtimestamp(self.modified, UTC).isoformat()
            if self.modified
            else None,
            "is_dir": self.is_dir,
        }


def _is_printable(name: str) -> bool:
    return Path(name).suffix.lower() in ALLOWED_SUFFIXES


def _join(base: str, name: str) -> str:
    return f"{base.rstrip('/')}/{name}" if base and base != "/" else f"/{name}"


# --------------------------------------------------------------------------- FTP
def _ftp_connect(storage: RemoteStorage) -> ftplib.FTP:
    password = decrypt_secret(storage.secret) or ""
    if storage.kind == StorageKind.FTPS.value:
        client: ftplib.FTP = ftplib.FTP_TLS(timeout=CONNECT_TIMEOUT)
    else:
        client = ftplib.FTP(timeout=CONNECT_TIMEOUT)
    client.connect(storage.host, storage.port or 21)
    client.login(storage.username or "anonymous", password)
    if isinstance(client, ftplib.FTP_TLS):
        client.prot_p()
    client.set_pasv(True)
    return client


def _ftp_list(storage: RemoteStorage, path: str) -> list[RemoteEntry]:
    client = _ftp_connect(storage)
    try:
        client.cwd(path or "/")
        entries: list[RemoteEntry] = []
        for name, facts in client.mlsd():
            if name in (".", ".."):
                continue
            is_dir = facts.get("type") == "dir"
            if not is_dir and (facts.get("type") not in ("file", None) or not _is_printable(name)):
                continue
            modified = None
            raw_time = facts.get("modify")
            if raw_time:
                try:
                    modified = datetime.strptime(raw_time, "%Y%m%d%H%M%S").replace(
                        tzinfo=UTC
                    ).timestamp()
                except ValueError:
                    modified = None
            entries.append(
                RemoteEntry(
                    name=name,
                    path=_join(path, name),
                    size=int(facts.get("size") or 0),
                    modified=modified,
                    is_dir=is_dir,
                )
            )
        return entries
    except ftplib.error_perm as exc:
        # Les serveurs anciens n'implementent pas MLSD: repli sur NLST, qui ne
        # distingue pas les dossiers des fichiers -- on ne peut alors naviguer
        # que dans les fichiers imprimables du repertoire courant.
        if "MLSD" not in str(exc).upper() and "500" not in str(exc):
            raise
        names = client.nlst()
        return [
            RemoteEntry(name=Path(n).name, path=_join(path, Path(n).name))
            for n in names
            if _is_printable(n)
        ]
    finally:
        try:
            client.quit()
        except ftplib.all_errors:  # pragma: no cover - fermeture best-effort
            client.close()


def _ftp_download(storage: RemoteStorage, remote_path: str, destination: Path) -> None:
    client = _ftp_connect(storage)
    try:
        with destination.open("wb") as handle:
            client.retrbinary(f"RETR {remote_path}", handle.write)
    finally:
        try:
            client.quit()
        except ftplib.all_errors:  # pragma: no cover
            client.close()


def _ftp_upload(storage: RemoteStorage, source: Path, remote_path: str) -> None:
    client = _ftp_connect(storage)
    try:
        with source.open("rb") as handle:
            client.storbinary(f"STOR {remote_path}", handle)
    finally:
        try:
            client.quit()
        except ftplib.all_errors:  # pragma: no cover
            client.close()


# -------------------------------------------------------------------------- SFTP
def _sftp_client(storage: RemoteStorage):
    import paramiko

    password = decrypt_secret(storage.secret) or ""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=storage.host,
        port=storage.port or 22,
        username=storage.username or None,
        password=password or None,
        timeout=CONNECT_TIMEOUT,
        allow_agent=False,
        look_for_keys=False,
    )
    return client, client.open_sftp()


def _sftp_list(storage: RemoteStorage, path: str) -> list[RemoteEntry]:
    client, sftp = _sftp_client(storage)
    try:
        entries: list[RemoteEntry] = []
        for attr in sftp.listdir_attr(path or "/"):
            is_dir = bool(attr.st_mode and stat.S_ISDIR(attr.st_mode))
            if not is_dir and not _is_printable(attr.filename):
                continue
            entries.append(
                RemoteEntry(
                    name=attr.filename,
                    path=_join(path, attr.filename),
                    size=int(attr.st_size or 0),
                    modified=float(attr.st_mtime) if attr.st_mtime else None,
                    is_dir=is_dir,
                )
            )
        return entries
    finally:
        sftp.close()
        client.close()


def _sftp_download(storage: RemoteStorage, remote_path: str, destination: Path) -> None:
    client, sftp = _sftp_client(storage)
    try:
        sftp.get(remote_path, str(destination))
    finally:
        sftp.close()
        client.close()


def _sftp_upload(storage: RemoteStorage, source: Path, remote_path: str) -> None:
    client, sftp = _sftp_client(storage)
    try:
        sftp.put(str(source), remote_path)
    finally:
        sftp.close()
        client.close()


# ------------------------------------------------------------------- API async
_DISPATCH = {
    StorageKind.FTP.value: (_ftp_list, _ftp_download, _ftp_upload),
    StorageKind.FTPS.value: (_ftp_list, _ftp_download, _ftp_upload),
    StorageKind.SFTP.value: (_sftp_list, _sftp_download, _sftp_upload),
}


def _dispatch(storage: RemoteStorage):
    handlers = _DISPATCH.get(storage.kind)
    if handlers is None:
        raise RemoteError(f"Type de depot inconnu: {storage.kind}")
    return handlers


async def list_remote(storage: RemoteStorage, path: str | None = None) -> list[RemoteEntry]:
    list_fn, _, _ = _dispatch(storage)
    target = path or storage.remote_path or "/"
    try:
        return await asyncio.to_thread(list_fn, storage, target)
    except Exception as exc:  # noqa: BLE001 - remonte une erreur unifiee a l'API
        raise RemoteError(f"Listing impossible ({storage.name}): {exc}") from exc


async def download_remote(storage: RemoteStorage, remote_path: str, destination: Path) -> None:
    _, download_fn, _ = _dispatch(storage)
    try:
        await asyncio.to_thread(download_fn, storage, remote_path, destination)
    except Exception as exc:  # noqa: BLE001
        raise RemoteError(f"Telechargement impossible ({remote_path}): {exc}") from exc


async def upload_remote(storage: RemoteStorage, source: Path, remote_path: str) -> None:
    _, _, upload_fn = _dispatch(storage)
    try:
        await asyncio.to_thread(upload_fn, storage, source, remote_path)
    except Exception as exc:  # noqa: BLE001
        raise RemoteError(f"Envoi impossible ({remote_path}): {exc}") from exc


async def test_connection(storage: RemoteStorage) -> int:
    """Verifie les identifiants et retourne le nombre de fichiers imprimables visibles."""
    entries = await list_remote(storage)
    return sum(1 for e in entries if not e.is_dir)


async def import_entry(
    session: AsyncSession, storage: RemoteStorage, entry: RemoteEntry
) -> tuple[GcodeFile | None, bool]:
    """Telecharge une entree distante et l'ajoute a la bibliotheque."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(entry.name).suffix) as handle:
        temp_path = Path(handle.name)
    try:
        await download_remote(storage, entry.path, temp_path)
        return await register_file(
            session,
            temp_path,
            entry.name,
            source="remote",
            remote_id=storage.id,
            remote_path=entry.path,
        )
    finally:
        temp_path.unlink(missing_ok=True)


async def sync_storage(session: AsyncSession, storage: RemoteStorage) -> dict[str, Any]:
    """Importe les fichiers du depot absents de la bibliotheque."""
    entries = await list_remote(storage)
    known = {
        path
        for (path,) in (
            await session.execute(
                select(GcodeFile.remote_path).where(GcodeFile.remote_id == storage.id)
            )
        ).all()
        if path
    }

    imported: list[str] = []
    errors: list[str] = []
    for entry in entries:
        # La synchronisation reste volontairement non recursive: seuls les
        # fichiers du repertoire configure sont importes, pas ses sous-dossiers.
        if entry.is_dir or entry.path in known:
            continue
        try:
            record, created = await import_entry(session, storage, entry)
        except (RemoteError, ValueError) as exc:
            errors.append(f"{entry.name}: {exc}")
            continue
        if record is not None and created:
            imported.append(record.filename)

    storage.last_sync = datetime.now(UTC)
    storage.last_error = "; ".join(errors)[:1000] or None
    return {
        "listed": len(entries),
        "imported": imported,
        "errors": errors,
    }
