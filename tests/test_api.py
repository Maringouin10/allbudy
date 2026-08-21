"""Tests d'integration de l'API, du televersement a la fin d'impression."""
from __future__ import annotations

import pytest

from tests.conftest import wait_until
from tests.sample_data import make_3mf, make_gcode

pytestmark = pytest.mark.asyncio


async def upload_gcode(client, name="piece.gcode", **kwargs):
    response = await client.post(
        "/api/files", files={"file": (name, make_gcode(**kwargs).encode(), "text/plain")}
    )
    assert response.status_code == 201, response.text
    return response.json()["file"]


# ------------------------------------------------------------------- auth
async def test_login_invalide(client):
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "faux"}
    )
    assert response.status_code == 401


async def test_endpoint_protege_sans_jeton(client):
    response = await client.get("/api/printers", headers={"Authorization": "Bearer nimporte-quoi"})
    assert response.status_code == 401


async def test_info_est_public(client):
    response = await client.get("/api/system/info", headers={"Authorization": ""})
    assert response.status_code == 200
    assert response.json()["name"] == "AllBudy"


# ------------------------------------------------------------------ files
async def test_upload_et_metadonnees(client):
    file = await upload_gcode(client)
    assert file["kind"] == "gcode"
    assert file["printable"] is True
    assert file["meta"]["filament_types"] == ["PLA"]
    assert file["meta"]["print_time_s"] == 4350
    assert file["thumbnail"]

    thumbnail = await client.get(f"/api/files/{file['id']}/thumbnail")
    assert thumbnail.status_code == 200
    assert thumbnail.headers["content-type"] == "image/png"


async def test_upload_doublon_dedoublonne(client):
    first = await upload_gcode(client, "a.gcode")
    response = await client.post(
        "/api/files", files={"file": ("copie.gcode", make_gcode().encode(), "text/plain")}
    )
    assert response.status_code == 201
    assert response.json()["created"] is False
    assert response.json()["file"]["id"] == first["id"]


async def test_upload_extension_refusee(client):
    response = await client.post("/api/files", files={"file": ("notes.txt", b"bonjour")})
    assert response.status_code == 415


async def test_telechargement_et_apercu(client):
    file = await upload_gcode(client)
    download = await client.get(f"/api/files/{file['id']}/download")
    assert download.status_code == 200
    assert b"G28" in download.content

    preview = await client.get(f"/api/files/{file['id']}/preview?lines=20")
    assert preview.status_code == 200
    assert len(preview.json()["lines"]) <= 20


async def test_3mf_non_tranche_refuse_en_file(client):
    response = await client.post(
        "/api/files", files={"file": ("projet.3mf", make_3mf(sliced=False), "model/3mf")}
    )
    file = response.json()["file"]
    assert file["printable"] is False

    job = await client.post("/api/jobs", json={"file_id": file["id"]})
    assert job.status_code == 400
    assert "tranche" in job.json()["detail"]


# --------------------------------------------------------------- printers
async def test_crud_imprimante(client):
    created = await client.post(
        "/api/printers",
        json={"name": "K1 atelier", "transport": "simulator", "host": "sim", "port": 0},
    )
    assert created.status_code == 201
    printer = created.json()

    listed = await client.get("/api/printers")
    assert [p["id"] for p in listed.json()] == [printer["id"]]

    patched = await client.patch(
        f"/api/printers/{printer['id']}", json={"nozzle_diameter": 0.6, "tags": ["abs"]}
    )
    assert patched.json()["nozzle_diameter"] == 0.6
    assert patched.json()["tags"] == ["abs"]

    duplicate = await client.post(
        "/api/printers", json={"name": "K1 atelier", "transport": "simulator", "host": "sim"}
    )
    assert duplicate.status_code == 409

    deleted = await client.delete(f"/api/printers/{printer['id']}")
    assert deleted.status_code == 200
    assert (await client.get("/api/printers")).json() == []


async def test_port_par_defaut_selon_protocole(client):
    moonraker = await client.post(
        "/api/printers", json={"name": "m", "transport": "moonraker", "host": "10.0.0.1"}
    )
    assert moonraker.json()["port"] == 7125
    lan = await client.post(
        "/api/printers", json={"name": "l", "transport": "creality_lan", "host": "10.0.0.2"}
    )
    assert lan.json()["port"] == 9999


async def test_commande_sur_imprimante_hors_ligne(client):
    printer = (
        await client.post(
            "/api/printers", json={"name": "absente", "transport": "moonraker", "host": "10.255.255.1"}
        )
    ).json()
    response = await client.post(f"/api/printers/{printer['id']}/pause")
    assert response.status_code == 409


async def test_statut_temps_reel(client, simulator):
    response = await client.get(f"/api/printers/{simulator['id']}/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is True
    assert payload["status"]["state"] == "idle"
    # Le simulateur expose un CFS a quatre emplacements.
    assert len(payload["status"]["spools"]) == 4


async def test_commandes_machine(client, simulator):
    printer_id = simulator["id"]
    assert (await client.post(f"/api/printers/{printer_id}/temperature",
                              json={"heater": "extruder", "value": 215})).status_code == 200
    assert (await client.post(f"/api/printers/{printer_id}/home",
                              json={"axes": "XY"})).status_code == 200
    assert (await client.post(f"/api/printers/{printer_id}/move",
                              json={"axis": "Z", "distance": 5})).status_code == 200
    assert (await client.post(f"/api/printers/{printer_id}/light",
                              json={"on": False})).status_code == 200

    invalide = await client.post(
        f"/api/printers/{printer_id}/temperature", json={"heater": "plasma", "value": 10}
    )
    assert invalide.status_code == 422


# ------------------------------------------------------------------ spools
async def test_cfs_synchronise_dans_inventaire(client, simulator):
    inventory = await wait_until(
        lambda: _inventory_ready(client, simulator["id"]), timeout=10
    )
    assert len(inventory) == 4
    assert all(spool["managed"] for spool in inventory)


async def _inventory_ready(client, printer_id):
    response = await client.get(f"/api/spools?printer_id={printer_id}")
    spools = response.json()
    return spools if len(spools) == 4 else None


async def test_bobine_manuelle(client):
    printer = (
        await client.post("/api/printers", json={"name": "sans-cfs", "transport": "simulator",
                                                 "host": "sim", "port": 0, "has_cfs": False})
    ).json()
    created = await client.post("/api/spools", json={
        "printer_id": printer["id"], "unit": -1, "slot": 0,
        "material": "PETG", "color_hex": "#00FF00", "vendor": "Creality",
    })
    assert created.status_code == 201
    assert created.json()["color_hex"] == "#00FF00"

    doublon = await client.post("/api/spools", json={
        "printer_id": printer["id"], "unit": -1, "slot": 0, "material": "PLA",
    })
    assert doublon.status_code == 409

    patched = await client.patch(f"/api/spools/{created.json()['id']}", json={"remaining_g": 250})
    assert patched.json()["remaining_g"] == 250
    assert patched.json()["managed"] is False


async def test_couleur_invalide_rejetee(client, simulator):
    response = await client.post("/api/spools", json={
        "printer_id": simulator["id"], "unit": 5, "slot": 0, "color_hex": "pas-une-couleur",
    })
    assert response.status_code == 422


# -------------------------------------------------------------------- file
async def test_cycle_complet_du_travail(client, simulator):
    """Un travail compatible doit etre attribue, imprime, puis marque termine."""
    from allbudy.printers.manager import manager

    # Le simulateur charge du PLA rouge (#E74C3C) dans l'emplacement 0.
    file = await upload_gcode(client, "cube.gcode", material="PLA", color="#E74C3C")
    await wait_until(lambda: _inventory_ready(client, simulator["id"]), timeout=10)

    # Impression courte pour garder le test rapide.
    runtime = manager.get(simulator["id"])
    runtime.transport.set_print_duration(3)

    job = (await client.post("/api/jobs", json={"file_id": file["id"]})).json()
    assert job["status"] == "queued"

    await wait_until(lambda: _job_status(client, job["id"], {"printing", "sending"}), timeout=15)
    printing = (await client.get(f"/api/jobs/{job['id']}")).json()
    assert printing["printer_id"] == simulator["id"]
    assert printing["remote_filename"]

    await wait_until(lambda: _job_status(client, job["id"], {"completed"}), timeout=25)
    done = (await client.get(f"/api/jobs/{job['id']}")).json()
    assert done["progress"] == 100.0
    assert done["copies_done"] == 1
    assert done["finished_at"]


async def _job_status(client, job_id, expected: set[str]):
    response = await client.get(f"/api/jobs/{job_id}")
    return response.json()["status"] in expected


async def test_travail_incompatible_reste_en_file(client, simulator):
    """Aucune bobine de la couleur demandee: le travail attend et l'explique."""
    file = await upload_gcode(client, "violet.gcode")
    await wait_until(lambda: _inventory_ready(client, simulator["id"]), timeout=10)

    job = (await client.post("/api/jobs", json={
        "file_id": file["id"], "required_material": "PLA", "required_color": "#8E44AD",
        "color_tolerance": 10,
    })).json()

    report = await client.get(f"/api/jobs/{job['id']}/match")
    assert report.status_code == 200
    verdicts = report.json()
    assert len(verdicts) == 1
    assert verdicts[0]["ok"] is False
    assert "aucune bobine" in verdicts[0]["reason"]

    still_queued = (await client.get(f"/api/jobs/{job['id']}")).json()
    assert still_queued["status"] == "queued"


async def test_reordonnancement_de_la_file(client):
    file = await upload_gcode(client)
    ids = []
    for index in range(3):
        job = await client.post("/api/jobs", json={"file_id": file["id"], "name": f"job-{index}",
                                                   "auto_start": False})
        ids.append(job.json()["id"])

    reversed_ids = list(reversed(ids))
    response = await client.post("/api/jobs/reorder", json={"job_ids": reversed_ids})
    assert response.status_code == 200

    jobs = (await client.get("/api/jobs?status=queued")).json()
    assert [job["id"] for job in jobs] == reversed_ids


async def test_reorder_avec_id_inconnu(client):
    response = await client.post("/api/jobs/reorder", json={"job_ids": [4242]})
    assert response.status_code == 404


async def test_annulation_et_relance(client):
    file = await upload_gcode(client)
    job = (await client.post("/api/jobs", json={"file_id": file["id"], "auto_start": False})).json()

    cancelled = await client.post(f"/api/jobs/{job['id']}/cancel")
    assert cancelled.status_code == 200
    assert (await client.get(f"/api/jobs/{job['id']}")).json()["status"] == "cancelled"

    requeued = await client.post(f"/api/jobs/{job['id']}/requeue")
    assert requeued.status_code == 200
    assert (await client.get(f"/api/jobs/{job['id']}")).json()["status"] == "queued"


async def test_suppression_fichier_utilise_refusee(client, simulator):
    from allbudy.printers.manager import manager

    file = await upload_gcode(client, "cube.gcode", material="PLA", color="#E74C3C")
    await wait_until(lambda: _inventory_ready(client, simulator["id"]), timeout=10)
    manager.get(simulator["id"]).transport.set_print_duration(30)

    await client.post("/api/jobs", json={"file_id": file["id"]})
    await wait_until(lambda: _any_job_printing(client), timeout=15)

    response = await client.delete(f"/api/files/{file['id']}")
    assert response.status_code == 409


async def _any_job_printing(client):
    jobs = (await client.get("/api/jobs?active=true")).json()
    return any(job["status"] in ("printing", "sending") for job in jobs)


async def test_dispatcher_suspendu(client, simulator):
    await client.post("/api/jobs/dispatcher?enabled=false")
    try:
        file = await upload_gcode(client, "attente.gcode", material="PLA", color="#E74C3C")
        job = (await client.post("/api/jobs", json={"file_id": file["id"]})).json()
        import asyncio

        await asyncio.sleep(1.5)
        assert (await client.get(f"/api/jobs/{job['id']}")).json()["status"] == "queued"
    finally:
        await client.post("/api/jobs/dispatcher?enabled=true")


# ----------------------------------------------------------------- systeme
async def test_stats_et_journal(client, simulator):
    stats = (await client.get("/api/system/stats")).json()
    assert stats["printers"]["total"] == 1
    assert "disk" in stats

    events = (await client.get("/api/system/events?limit=10")).json()
    assert any("connectee" in event["message"] for event in events)


async def test_decouverte_refuse_reseau_trop_large(client):
    response = await client.get("/api/printers/discover?network=10.0.0.0/8")
    assert response.status_code == 400
    assert "trop large" in response.json()["detail"]


async def test_websocket_pousse_les_etats(client, simulator):
    """Le WebSocket envoie l'instantane a la connexion."""
    from allbudy.events import bus

    queue = await bus.subscribe()
    try:
        bus.publish("test.event", {"valeur": 1})
        message = await queue.get()
        assert message["type"] == "test.event"
    finally:
        await bus.unsubscribe(queue)
