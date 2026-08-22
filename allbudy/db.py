"""Acces base de donnees (SQLite async)."""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import get_settings
from .models import Base

log = logging.getLogger("allbudy.db")

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine, _sessionmaker
    if _engine is None:
        settings = get_settings()
        settings.ensure_dirs()
        _engine = create_async_engine(settings.database_url, echo=False, future=True)

        @event.listens_for(_engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA synchronous=NORMAL")
            # Le dispatcher, les boucles d'interrogation et les requetes HTTP
            # ecrivent en parallele: on attend le verrou au lieu d'echouer.
            cur.execute("PRAGMA busy_timeout=8000")
            cur.close()

        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


def _column_default_sql(column) -> str | None:
    """Litteral SQL pour DEFAULT, ou None si aucun defaut simple n'est connu."""
    default = column.default
    if default is None:
        return None
    if default.is_scalar:
        value = default.arg
    elif default.is_callable:
        # SQLAlchemy enveloppe les callables sous la forme `lambda ctx: fn()`.
        value = default.arg(None)
    else:
        return None
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, (list, dict)):
        # Colonnes JSON (SQLAlchemy les stocke en texte sur SQLite).
        return "'" + json.dumps(value).replace("'", "''") + "'"
    return None


async def _add_missing_columns(conn, metadata=None) -> None:
    """Ajoute les colonnes manquantes sur les tables deja existantes.

    Ce projet n'a pas d'outil de migration (Alembic): `create_all` cree les
    tables absentes mais ne touche jamais une table deja presente. Sans ce
    garde-fou, une instance existante mise a jour vers une version qui ajoute
    une colonne (ex: Job.required_filaments) plante au premier acces a cette
    colonne. On compare donc le modele declare a `PRAGMA table_info` et on
    complete les colonnes manquantes, en donnant a chacune le meme defaut que
    celui declare cote Python.

    `metadata` est parametrable pour les tests; l'appel reel utilise toujours
    celui de l'application (`Base.metadata`).
    """
    dialect = conn.dialect
    for table in (metadata or Base.metadata).tables.values():
        rows = (await conn.execute(text(f'PRAGMA table_info("{table.name}")'))).all()
        existing = {row[1] for row in rows}
        if not existing:
            continue  # table absente: create_all vient de la creer a jour
        for column in table.columns:
            if column.name in existing:
                continue
            col_type = column.type.compile(dialect=dialect)
            default_sql = _column_default_sql(column)
            clause = f" DEFAULT {default_sql}" if default_sql is not None else ""
            await conn.execute(
                text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}{clause}')
            )
            log.info("Migration: colonne ajoutee %s.%s", table.name, column.name)


async def init_db() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _add_missing_columns(conn)
        await conn.execute(text("PRAGMA foreign_keys=ON"))


async def dispose_db() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Session transactionnelle pour le code hors requete HTTP (workers)."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """Dependance FastAPI."""
    async with get_sessionmaker()() as session:
        yield session
