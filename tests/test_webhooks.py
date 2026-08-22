"""Webhooks sortants: portee (tout/etiquette/imprimante) et cycle bed_cold."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from allbudy.db import session_scope
from allbudy.models import Printer, Webhook
from allbudy.printers.base import PrinterStatus
from allbudy.printers.manager import PrinterManager, PrinterRuntime
from allbudy.webhooks import notify_print_finished


class _FakeResponse:
    def raise_for_status(self):
        pass


class _FakeHttpClient:
    """Remplace httpx.AsyncClient: enregistre les appels, ne fait aucun reseau."""

    calls: list[tuple[str, dict]] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json):
        _FakeHttpClient.calls.append((url, json))
        return _FakeResponse()


@pytest.fixture(autouse=True)
def _no_real_http(monkeypatch):
    """Isole allbudy.webhooks du vrai module httpx: la fixture `client`
    (conftest.py) utilise elle aussi httpx.AsyncClient pour parler a l'ASGI
    de test, donc on ne doit surtout pas patcher le module httpx global."""
    _FakeHttpClient.calls = []
    import allbudy.webhooks as webhooks_module

    monkeypatch.setattr(webhooks_module, "httpx", SimpleNamespace(AsyncClient=_FakeHttpClient))
    yield


async def _make_printer(session, name, **kwargs) -> Printer:
    printer = Printer(name=name, transport="simulator", host="sim", port=0, **kwargs)
    session.add(printer)
    await session.flush()
    return printer


async def test_notify_print_finished_respecte_la_portee(client):
    async with session_scope() as session:
        cible = await _make_printer(session, "k2-cible", tags=["atelier"])
        autre = await _make_printer(session, "k2-autre")
        session.add(Webhook(event="print_finished", url="https://hook/all"))
        session.add(Webhook(event="print_finished", url="https://hook/tag", tag="atelier"))
        session.add(
            Webhook(event="print_finished", url="https://hook/printer", printer_id=cible.id)
        )
        session.add(
            Webhook(event="print_finished", url="https://hook/autre-imprimante", printer_id=autre.id)
        )
        await session.flush()
        # Un objet minimal suffit: notify_print_finished ne lit que id/name,
        # pas besoin d'un vrai fichier/travail persiste pour ce test.
        job = SimpleNamespace(id=1, name="piece")

        await notify_print_finished(session, cible, job)

    urls = {url for url, _ in _FakeHttpClient.calls}
    assert urls == {"https://hook/all", "https://hook/tag", "https://hook/printer"}


async def test_bed_cold_notifie_une_fois_puis_desarme(client):
    async with session_scope() as session:
        printer = await _make_printer(session, "k2-froid")
        webhook = Webhook(
            event="bed_cold", url="https://hook/froid", printer_id=printer.id,
            bed_cold_threshold=40.0,
        )
        session.add(webhook)
        await session.flush()
        printer_id, webhook_id = printer.id, webhook.id

    manager = PrinterManager()
    runtime = PrinterRuntime(Printer(id=printer_id, name="k2", transport="simulator"))
    manager._runtimes[printer_id] = runtime
    manager.arm_bed_cold(printer_id)
    assert runtime.bed_cold_armed is True

    # Encore chaud: aucune notification.
    await manager._check_bed_cold(runtime, PrinterStatus(bed_temp=60.0))
    await asyncio.sleep(0.05)
    assert _FakeHttpClient.calls == []
    assert runtime.bed_cold_armed is True

    # Passe sous le seuil: notifie une fois (l'appel HTTP part en tache de
    # fond, on laisse la boucle tourner), puis se desarme.
    await manager._check_bed_cold(runtime, PrinterStatus(bed_temp=35.0))
    await asyncio.sleep(0.05)
    assert [url for url, _ in _FakeHttpClient.calls] == ["https://hook/froid"]
    assert runtime.bed_cold_armed is False
    assert webhook_id in runtime.bed_cold_notified

    # Un appel supplementaire (ex: reste bas quelques cycles) ne renotifie pas.
    await manager._check_bed_cold(runtime, PrinterStatus(bed_temp=30.0))
    await asyncio.sleep(0.05)
    assert len(_FakeHttpClient.calls) == 1
