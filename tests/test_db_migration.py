"""Ajout de colonnes manquantes sur une base existante (pas d'Alembic ici).

Simule le cas reel: une instance deja deployee dont la base ne connait pas
encore une colonne ajoutee par une version plus recente du modele.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Boolean, Column, Integer, MetaData, String, Table, Text, text
from sqlalchemy.ext.asyncio import create_async_engine

from allbudy.db import _add_missing_columns

pytestmark = pytest.mark.asyncio


def _new_schema() -> MetaData:
    """Version "actuelle" du modele: une colonne JSON de plus que l'ancienne."""
    metadata = MetaData()
    Table(
        "widgets",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String(64), nullable=False),
        Column("enabled", Boolean, default=True, nullable=False),
        Column("required_filaments", Text, default=list, nullable=False),
    )
    return metadata


async def test_ajoute_une_colonne_manquante_avec_son_defaut(tmp_path: Path):
    db_path = tmp_path / "old.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        async with engine.begin() as conn:
            # Schema "ancien": la table existe deja, sans la nouvelle colonne.
            await conn.execute(
                text(
                    "CREATE TABLE widgets ("
                    "id INTEGER PRIMARY KEY, name VARCHAR(64) NOT NULL, "
                    "enabled BOOLEAN NOT NULL DEFAULT 1)"
                )
            )
            # Une ligne preexistante: la migration doit lui donner un defaut
            # valide plutot que de planter (NOT NULL sans valeur).
            await conn.execute(text("INSERT INTO widgets (name, enabled) VALUES ('a', 1)"))

        async with engine.begin() as conn:
            await _add_missing_columns(conn, metadata=_new_schema())

        async with engine.begin() as conn:
            columns = {row[1] for row in (await conn.execute(text("PRAGMA table_info(widgets)"))).all()}
            assert "required_filaments" in columns

            # La ligne preexistante recoit bien le defaut Python (list -> "[]").
            existing_value = (
                await conn.execute(text("SELECT required_filaments FROM widgets WHERE name='a'"))
            ).scalar_one()
            assert existing_value == "[]"

            # Une insertion normale continue de fonctionner ensuite.
            await conn.execute(text("INSERT INTO widgets (name, enabled) VALUES ('b', 0)"))
    finally:
        await engine.dispose()


async def test_ne_touche_pas_une_table_deja_a_jour(tmp_path: Path):
    """Rejouer la migration sur une base deja a jour ne doit rien casser."""
    db_path = tmp_path / "current.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        schema = _new_schema()
        async with engine.begin() as conn:
            await conn.run_sync(schema.create_all)
            await _add_missing_columns(conn, metadata=schema)
            # Deuxieme passage: aucune colonne a ajouter, ne doit pas lever.
            await _add_missing_columns(conn, metadata=schema)
    finally:
        await engine.dispose()
