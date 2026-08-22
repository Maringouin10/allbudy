"""Depots distants: navigation par dossiers, import non recursif."""
from __future__ import annotations

import pytest

from allbudy.files.remotes import RemoteEntry, sync_storage
from allbudy.models import RemoteStorage


def test_remote_entry_to_dict_porte_is_dir():
    fichier = RemoteEntry(name="a.gcode", path="/a.gcode")
    dossier = RemoteEntry(name="sous-dossier", path="/sous-dossier", is_dir=True)
    assert fichier.to_dict()["is_dir"] is False
    assert dossier.to_dict()["is_dir"] is True


@pytest.mark.asyncio
async def test_sync_storage_ignore_les_dossiers(monkeypatch):
    """La synchronisation ne doit jamais tenter de televerser un dossier."""
    from allbudy.files import remotes

    entries = [
        RemoteEntry(name="pieces", path="/pieces", is_dir=True),
        RemoteEntry(name="cube.gcode", path="/cube.gcode", size=10),
    ]

    async def fake_list_remote(storage, path=None):
        return entries

    imported_paths: list[str] = []

    async def fake_import_entry(session, storage, entry):
        imported_paths.append(entry.path)
        return None, True

    monkeypatch.setattr(remotes, "list_remote", fake_list_remote)
    monkeypatch.setattr(remotes, "import_entry", fake_import_entry)

    storage = RemoteStorage(id=1, name="nas", kind="sftp", host="nas.local", remote_path="/")

    class _FakeResult:
        def all(self):
            return []

    class _FakeSession:
        async def execute(self, *_args, **_kwargs):
            return _FakeResult()

    result = await sync_storage(_FakeSession(), storage)
    assert imported_paths == ["/cube.gcode"]
    assert result["listed"] == 2
