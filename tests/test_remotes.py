"""Depots distants: navigation par dossiers, import non recursif."""
from __future__ import annotations

import stat
from dataclasses import dataclass

import pytest

from allbudy.files.remotes import RemoteEntry, list_remote_tree, sync_storage
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


@dataclass
class _FakeAttr:
    filename: str
    st_mode: int
    st_size: int = 0
    st_mtime: float | None = None


class _FakeSftp:
    """Arborescence figee: /=[a(dir), a/b(dir), a/b/c(dir), a/b/c/d(dir)] + un fichier a la racine."""

    def __init__(self):
        self._tree = {
            "/": [
                _FakeAttr("a", stat.S_IFDIR),
                _FakeAttr("cube.gcode", stat.S_IFREG),
            ],
            "/a": [_FakeAttr("b", stat.S_IFDIR)],
            "/a/b": [_FakeAttr("c", stat.S_IFDIR)],
            "/a/b/c": [_FakeAttr("d", stat.S_IFDIR)],
        }

    def listdir_attr(self, path):
        return self._tree.get(path, [])

    def close(self):
        pass


class _FakeSshClient:
    def close(self):
        pass


@pytest.mark.asyncio
async def test_list_remote_tree_respecte_la_profondeur(monkeypatch):
    """La marche recursive s'arrete a `depth` niveaux, sans lister les fichiers."""
    from allbudy.files import remotes

    monkeypatch.setattr(remotes, "_sftp_client", lambda storage: (_FakeSshClient(), _FakeSftp()))

    storage = RemoteStorage(id=1, name="nas", kind="sftp", host="nas.local", remote_path="/")
    tree = await list_remote_tree(storage, depth=2)

    assert tree["path"] == "/"
    assert [c["name"] for c in tree["children"]] == ["a"]
    b = tree["children"][0]["children"]
    assert [c["name"] for c in b] == ["b"]
    # Profondeur 2 depuis la racine: on s'arrete a "a/b", "c" n'est pas descendu.
    assert b[0]["children"] == []


@pytest.mark.asyncio
async def test_list_remote_tree_limite_le_nombre_de_noeuds(monkeypatch):
    """Un depot avec beaucoup de dossiers ne doit pas faire exploser le nombre d'appels."""
    from allbudy.files import remotes

    monkeypatch.setattr(remotes, "_sftp_client", lambda storage: (_FakeSshClient(), _FakeSftp()))
    monkeypatch.setattr(remotes, "MAX_TREE_NODES", 1)

    storage = RemoteStorage(id=1, name="nas", kind="sftp", host="nas.local", remote_path="/")
    tree = await list_remote_tree(storage, depth=4)

    # Un seul dossier au total doit avoir ete compte, meme si l'arborescence en a plus.
    assert len(tree["children"]) == 1
    assert tree["children"][0]["children"] == []
