"""Fixtures partagees: instance isolee d'AllBudy par test."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Repertoire de donnees dedie, isole de l'instance de developpement."""
    target = tmp_path / "data"
    monkeypatch.setenv("ALLBUDY_DATA_DIR", str(target))
    monkeypatch.setenv("ALLBUDY_SECRET_KEY", "cle-de-test-non-secrete")
    monkeypatch.setenv("ALLBUDY_ADMIN_PASSWORD", "motdepasse-test")
    monkeypatch.setenv("ALLBUDY_POLL_INTERVAL", "0.2")
    monkeypatch.setenv("ALLBUDY_SCHEDULER_INTERVAL", "0.3")
    monkeypatch.setenv("ALLBUDY_DEMO_PRINTERS", "0")

    from allbudy.config import get_settings

    get_settings.cache_clear()
    target.mkdir(parents=True, exist_ok=True)
    yield target
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def client(data_dir: Path):
    """Client HTTP branche sur l'app ASGI, avec cycle de vie complet.

    Porte un jeton valide meme si l'authentification est desactivee (le cas
    par defaut du produit): les appels marchent dans les deux configurations.
    """
    import httpx

    from allbudy.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", timeout=30
        ) as http:
            response = await http.post(
                "/api/auth/login", json={"username": "admin", "password": "motdepasse-test"}
            )
            assert response.status_code == 200, response.text
            http.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
            yield http


@pytest_asyncio.fixture
async def client_auth_enabled(data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Client sur une instance ou l'authentification est explicitement activee."""
    import httpx

    from allbudy.config import get_settings
    from allbudy.main import create_app

    monkeypatch.setenv("ALLBUDY_AUTH_ENABLED", "true")
    get_settings.cache_clear()

    app = create_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", timeout=30
        ) as http:
            yield http
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def simulator(client):
    """Une imprimante simulee connectee et prete."""
    response = await client.post(
        "/api/printers",
        json={
            "name": "Banc de test",
            "model": "K1 Max",
            "transport": "simulator",
            "host": "simulateur",
            "port": 0,
            "has_cfs": True,
        },
    )
    assert response.status_code == 201, response.text
    printer = response.json()
    await wait_until(lambda: _connected(printer["id"]), timeout=10)
    return printer


def _connected(printer_id: int) -> bool:
    from allbudy.printers.manager import manager

    runtime = manager.get(printer_id)
    # `connected` flips true right after transport.connect(), before the
    # first refresh() populates `status`: wait for that too, otherwise a
    # test can observe a "connected" printer still reporting the default
    # offline status (and so, wrongly, not free).
    return bool(runtime and runtime.connected and runtime.status.online)


async def wait_until(predicate, timeout: float = 10.0, interval: float = 0.15):
    """Attend qu'une condition (sync ou async) devienne vraie."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return result
        await asyncio.sleep(interval)
    raise AssertionError(f"Condition non remplie en {timeout} s")
