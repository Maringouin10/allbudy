"""Point d'entree de l'application AllBudy."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from . import __version__
from .api import auth, files, jobs, printers, spools, storage, system, ws
from .config import get_settings
from .db import dispose_db, init_db, session_scope
from .events import record_event
from .files.remotes import RemoteError, sync_storage
from .models import Printer, RemoteStorage, TransportKind
from .printers.base import PrinterError
from .printers.manager import manager
from .queueing import scheduler
from .security import ensure_admin_user

log = logging.getLogger("allbudy")

WEB_DIR = Path(__file__).parent / "web"


def configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-22s %(message)s",
        datefmt="%H:%M:%S",
    )
    # Ces bibliotheques sont bavardes au niveau DEBUG.
    for noisy in ("httpx", "websockets.client", "paramiko.transport"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def seed_demo_printers() -> None:
    """Cree des imprimantes simulees au premier demarrage si demande."""
    settings = get_settings()
    if settings.demo_printers <= 0:
        return
    async with session_scope() as session:
        if (await session.execute(select(Printer.id).limit(1))).first():
            return
        for index in range(settings.demo_printers):
            session.add(
                Printer(
                    name=f"Demo K1-{index + 1}",
                    model="K1 Max" if index % 2 else "K1C",
                    transport=TransportKind.SIMULATOR.value,
                    host="simulateur",
                    port=0,
                    has_cfs=True,
                    tags=["demo"],
                    sort_order=index,
                )
            )
    log.info("%d imprimante(s) simulee(s) creee(s)", settings.demo_printers)


async def remote_sync_worker() -> None:
    """Synchronise periodiquement les depots marques en import automatique."""
    while True:
        try:
            async with session_scope() as session:
                storages = (
                    (
                        await session.execute(
                            select(RemoteStorage).where(
                                RemoteStorage.enabled.is_(True),
                                RemoteStorage.auto_import.is_(True),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                now = datetime.now(UTC)
                for remote in storages:
                    due = remote.last_sync is None or remote.last_sync + timedelta(
                        seconds=remote.sync_interval
                    ) <= now
                    if not due:
                        continue
                    try:
                        result = await sync_storage(session, remote)
                    except RemoteError as exc:
                        remote.last_error = str(exc)[:1000]
                        remote.last_sync = now
                        log.warning("Synchro %s echouee: %s", remote.name, exc)
                        continue
                    if result["imported"]:
                        await record_event(
                            f"Depot {remote.name}: {len(result['imported'])} fichier(s) importe(s)",
                            category="storage",
                        )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - le worker ne doit jamais mourir
            log.exception("Erreur du worker de synchronisation")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    log.info("Demarrage d'AllBudy %s (donnees: %s)", __version__, settings.data_dir.resolve())

    await init_db()
    await ensure_admin_user()
    await seed_demo_printers()
    await manager.start()
    await scheduler.start()
    sync_task = asyncio.create_task(remote_sync_worker(), name="remote-sync")

    try:
        yield
    finally:
        log.info("Arret d'AllBudy")
        sync_task.cancel()
        try:
            await sync_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        await scheduler.stop()
        await manager.stop()
        await dispose_db()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AllBudy",
        description="Gestion de ferme d'impression 3D Creality, 100% locale.",
        version=__version__,
        lifespan=lifespan,
        root_path=settings.base_path,
    )

    @app.exception_handler(PrinterError)
    async def printer_error_handler(_request: Request, exc: PrinterError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    for module in (auth, system, printers, files, jobs, spools, storage):
        app.include_router(module.router)
    app.include_router(ws.router)

    if WEB_DIR.exists():
        app.mount(
            "/assets", StaticFiles(directory=WEB_DIR, html=False), name="assets"
        )

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html")

        @app.get("/{page}", include_in_schema=False)
        async def spa_fallback(page: str) -> FileResponse:
            """Routes cote client: tout ce qui n'est pas un fichier retombe sur l'app."""
            candidate = (WEB_DIR / page).resolve()
            if candidate.is_file() and WEB_DIR.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(WEB_DIR / "index.html")

    return app


app = create_app()


def main() -> None:  # pragma: no cover - lancement CLI
    import uvicorn

    settings = get_settings()
    configure_logging()
    uvicorn.run(
        "allbudy.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
