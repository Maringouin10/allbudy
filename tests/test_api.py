"""Tests d'integration de l'API, du televersement a la fin d'impression."""
from __future__ import annotations

import asyncio

import pytest

from tests.conftest import _connected, wait_until
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


async def test_api_ouverte_sans_authentification_par_defaut(client):
    """AllBudy est concu pour un reseau local: pas de jeton exige par defaut."""
    response = await client.get("/api/printers", headers={"Authorization": ""})
    assert response.status_code == 200


async def test_authentification_activable(client_auth_enabled):
    """Avec ALLBUDY_AUTH_ENABLED=true, la protection redevient effective."""
    sans_jeton = await client_auth_enabled.get("/api/printers")
    assert sans_jeton.status_code == 401

    jeton_invalide = await client_auth_enabled.get(
        "/api/printers", headers={"Authorization": "Bearer nimporte-quoi"}
    )
    assert jeton_invalide.status_code == 401

    connexion = await client_auth_enabled.post(
        "/api/auth/login", json={"username": "admin", "password": "motdepasse-test"}
    )
    assert connexion.status_code == 200
    token = connexion.json()["access_token"]

    avec_jeton = await client_auth_enabled.get(
        "/api/printers", headers={"Authorization": f"Bearer {token}"}
    )
    assert avec_jeton.status_code == 200


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

    # Une imprimante virtuelle "K1 (virtuelle)" est creee automatiquement pour
    # le meme modele (voir sync_virtual_printers): la liste en contient deux.
    listed = (await client.get("/api/printers")).json()
    reels = [p for p in listed if p["transport"] != "virtual"]
    virtuelles = [p for p in listed if p["transport"] == "virtual"]
    assert [p["id"] for p in reels] == [printer["id"]]
    assert [v["model"] for v in virtuelles] == ["K1"]

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
    # La virtuelle associee n'est jamais supprimee automatiquement.
    remaining = (await client.get("/api/printers")).json()
    assert [p["transport"] for p in remaining] == ["virtual"]


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


async def test_plateau_non_vide_bloque_le_travail_suivant(client, simulator):
    """Le plateau doit etre confirme vide avant qu'un 2e travail ne parte."""
    from allbudy.printers.manager import manager

    file = await upload_gcode(client, "cube.gcode", material="PLA", color="#E74C3C")
    await wait_until(lambda: _inventory_ready(client, simulator["id"]), timeout=10)

    runtime = manager.get(simulator["id"])
    runtime.transport.set_print_duration(3)

    first = (await client.post("/api/jobs", json={"file_id": file["id"]})).json()
    await wait_until(lambda: _job_status(client, first["id"], {"completed"}), timeout=25)

    printer = (await client.get(f"/api/printers/{simulator['id']}")).json()
    assert printer["bed_cleared"] is False

    second = (await client.post("/api/jobs", json={"file_id": file["id"]})).json()
    # Le plateau n'est pas confirme vide: le 2e travail reste en file quelques
    # cycles du dispatcher, jamais attribue.
    await asyncio.sleep(1)
    stuck = (await client.get(f"/api/jobs/{second['id']}")).json()
    assert stuck["status"] == "queued"
    assert stuck["printer_id"] is None

    cleared = await client.patch(f"/api/printers/{simulator['id']}", json={"bed_cleared": True})
    assert cleared.json()["bed_cleared"] is True

    await wait_until(lambda: _job_status(client, second["id"], {"printing", "sending", "completed"}), timeout=15)


async def test_echec_final_bloque_aussi_le_travail_suivant(client, simulator, monkeypatch):
    """Un travail en echec definitif laisse peut-etre une piece ratee sur le
    plateau: comme pour une reussite, il faut confirmer avant de renvoyer."""
    import sys

    from allbudy.printers.manager import manager

    # Le paquet "queueing" reexpose son attribut "scheduler" comme l'instance
    # du dispatcher (voir queueing/__init__.py): on prend le vrai module via
    # sys.modules pour patcher sa constante.
    scheduler_module = sys.modules["allbudy.queueing.scheduler"]

    # Une seule tentative autorisee: le premier echec est donc definitif.
    monkeypatch.setattr(scheduler_module, "MAX_ATTEMPTS", 1)

    file = await upload_gcode(client, "cube.gcode", material="PLA", color="#E74C3C")
    await wait_until(lambda: _inventory_ready(client, simulator["id"]), timeout=10)

    runtime = manager.get(simulator["id"])
    runtime.transport.set_print_duration(10)

    job = (await client.post("/api/jobs", json={"file_id": file["id"]})).json()
    await wait_until(lambda: _job_status(client, job["id"], {"printing", "sending"}), timeout=15)

    await client.post(f"/api/printers/{simulator['id']}/emergency-stop")
    await wait_until(lambda: _job_status(client, job["id"], {"failed"}), timeout=15)

    printer = (await client.get(f"/api/printers/{simulator['id']}")).json()
    assert printer["bed_cleared"] is False


async def test_imprimante_virtuelle_flux_complet(client, monkeypatch):
    """Imprimer -> envoi NAS -> demarrage confirme -> fin confirmee -> plateau a confirmer."""
    import sys

    # `allbudy.queueing.scheduler` (attribut de paquet) est le singleton
    # JobScheduler, pas le module: `allbudy/queueing/__init__.py` reexporte
    # l'instance sous le meme nom. Le vrai module s'obtient via sys.modules.
    scheduler_module = sys.modules["allbudy.queueing.scheduler"]

    uploads = []

    async def fake_upload_remote(storage, path, remote_path):
        uploads.append((storage.name, remote_path))

    monkeypatch.setattr(scheduler_module, "upload_remote", fake_upload_remote)

    real = (await client.post("/api/printers", json={
        "name": "K1C reel", "model": "K1C", "transport": "simulator", "host": "sim", "port": 0,
    })).json()

    printers = (await client.get("/api/printers")).json()
    virtual = next(p for p in printers if p["transport"] == "virtual" and p["model"] == "K1C")
    assert virtual["id"] != real["id"]
    await wait_until(lambda: _connected(virtual["id"]), timeout=10)

    storage = (await client.post("/api/storage", json={
        "name": "nas-virtuel", "kind": "sftp", "host": "nas.local", "remote_path": "/gcode",
    })).json()
    patched = await client.patch(
        f"/api/printers/{virtual['id']}", json={"virtual_target_storage_id": storage["id"]}
    )
    assert patched.json()["virtual_target_storage_id"] == storage["id"]

    # La virtuelle n'a pas de CFS: on declare la bobine nominalement chargee,
    # comme pour toute machine sans systeme de changement automatique.
    spool = await client.post("/api/spools", json={
        "printer_id": virtual["id"], "unit": -1, "slot": 0,
        "material": "PLA", "color_hex": "#FF0000", "active": True,
    })
    assert spool.status_code == 201

    file = await upload_gcode(client, "virtuel.gcode")
    job = (
        await client.post(
            "/api/jobs", json={"file_id": file["id"], "printer_id": virtual["id"]}
        )
    ).json()

    # Le fichier part sur le depot NAS configure, pas sur une machine.
    await wait_until(lambda: bool(uploads), timeout=10)
    assert uploads[0][0] == "nas-virtuel"

    # Pas de vraie impression avant confirmation manuelle: le travail reste "assigned".
    await wait_until(lambda: _job_status(client, job["id"], {"assigned"}), timeout=10)
    await asyncio.sleep(0.5)
    assert (await client.get(f"/api/jobs/{job['id']}")).json()["status"] == "assigned"

    # Une imprimante reelle ne peut pas confirmer un demarrage virtuel.
    refused = await client.post(f"/api/printers/{real['id']}/virtual/start")
    assert refused.status_code == 400

    started = await client.post(f"/api/printers/{virtual['id']}/virtual/start")
    assert started.status_code == 200
    await wait_until(lambda: _job_status(client, job["id"], {"printing"}), timeout=10)

    finished = await client.post(f"/api/printers/{virtual['id']}/virtual/finish")
    assert finished.status_code == 200
    await wait_until(lambda: _job_status(client, job["id"], {"completed"}), timeout=10)

    virtual_after = (await client.get(f"/api/printers/{virtual['id']}")).json()
    assert virtual_after["bed_cleared"] is False


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
    verdict = next(v for v in verdicts if v["printer_id"] == simulator["id"])
    assert verdict["ok"] is False
    assert "aucune bobine" in verdict["reason"]

    still_queued = (await client.get(f"/api/jobs/{job['id']}")).json()
    assert still_queued["status"] == "queued"


async def test_required_filaments_multi_couleur(client, simulator):
    """Une couleur par bobine choisie, plutot que devinee depuis le fichier."""
    file = await upload_gcode(client, "bicolore.gcode")
    await wait_until(lambda: _inventory_ready(client, simulator["id"]), timeout=10)

    # Le simulateur charge PLA rouge (#E74C3C) et PLA vert (#27AE60) par defaut.
    job = (await client.post("/api/jobs", json={
        "file_id": file["id"],
        "required_filaments": [
            {"material": "PLA", "color": "#E74C3C"},
            {"material": "PLA", "color": "#27AE60"},
        ],
    })).json()
    assert job["required_filaments"] == [
        {"material": "PLA", "color": "#E74C3C"},
        {"material": "PLA", "color": "#27AE60"},
    ]

    await wait_until(lambda: _job_status(client, job["id"], {"printing", "sending"}), timeout=15)
    dispatched = (await client.get(f"/api/jobs/{job['id']}")).json()
    assert dispatched["printer_id"] == simulator["id"]


async def test_required_filaments_partiel_avec_auto(client):
    """Une entree null dans required_filaments retombe sur les metadonnees du fichier."""
    file = await upload_gcode(client, "mix.gcode", material="PLA", color="#E74C3C")
    response = await client.post("/api/jobs", json={
        "file_id": file["id"], "required_filaments": [None],
    })
    assert response.status_code == 201
    match = await client.get(f"/api/jobs/{response.json()['id']}/match")
    # Une seule exigence (celle du fichier): reprend le comportement d'origine.
    assert match.status_code == 200


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
    # Les statistiques du tableau de bord ignorent le parc virtuel (NAS).
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


async def test_stats_imprimantes_libres_et_pieces(client, simulator):
    """Le tableau de bord compte les machines disponibles et les pieces sorties."""
    from allbudy.printers.manager import manager

    # La virtuelle auto-creee (meme modele) se connecte elle aussi en tache
    # de fond: laisser le temps a son premier cycle avant de compter.
    printers = (await client.get("/api/printers")).json()
    virtual = next(p for p in printers if p["transport"] == "virtual")
    await wait_until(lambda: _connected(virtual["id"]), timeout=10)

    stats = (await client.get("/api/system/stats")).json()
    # Le tableau de bord ignore le parc virtuel (NAS): seule la reelle compte.
    assert stats["printers"]["free"] == 1
    assert stats["jobs"]["pieces_7d"] == 0

    # Un plateau de 3 pieces, imprime une fois -> 3 pieces au compteur.
    gcode = make_gcode(material="PLA", color="#E74C3C") + (
        "\nEXCLUDE_OBJECT_DEFINE NAME=A\nEXCLUDE_OBJECT_DEFINE NAME=B\n"
        "EXCLUDE_OBJECT_DEFINE NAME=C\n"
    )
    response = await client.post(
        "/api/files", files={"file": ("trois.gcode", gcode.encode(), "text/plain")}
    )
    file = response.json()["file"]
    assert file["meta"]["object_count"] == 3

    await wait_until(lambda: _inventory_ready(client, simulator["id"]), timeout=10)
    manager.get(simulator["id"]).transport.set_print_duration(2)
    job = (await client.post("/api/jobs", json={"file_id": file["id"]})).json()

    # Pendant l'impression, la machine n'est plus comptee comme libre (la
    # virtuelle est ignoree des stats). Le compte vient du cache d'etat du
    # parc, rafraichi par la boucle d'interrogation: il peut accuser un
    # intervalle de retard sur le statut du travail.
    await wait_until(lambda: _job_status(client, job["id"], {"printing"}), timeout=15)
    await wait_until(lambda: _free_printers(client, 0), timeout=10)

    await wait_until(lambda: _job_status(client, job["id"], {"completed"}), timeout=25)
    done = (await client.get("/api/system/stats")).json()
    assert done["jobs"]["completed_7d"] == 1
    assert done["jobs"]["pieces_7d"] == 3


async def test_ventilateurs_exposes_dans_letat(client, simulator):
    status = (await client.get(f"/api/printers/{simulator['id']}/status")).json()
    fans = status["status"]["fans"]
    assert [fan["label"] for fan in fans] == ["Piece", "Auxiliaire", "Chambre", "Tete"]
    assert fans[0]["controllable"] is True
    # Le ventilateur de tete est en lecture seule.
    assert fans[-1]["controllable"] is False

    response = await client.post(
        f"/api/printers/{simulator['id']}/fan", json={"fan": "part", "speed": 80}
    )
    assert response.status_code == 200


async def _free_printers(client, expected: int):
    stats = (await client.get("/api/system/stats")).json()
    return stats["printers"]["free"] == expected


async def test_crud_webhook(client):
    printer = (
        await client.post(
            "/api/printers",
            json={"name": "k2", "transport": "simulator", "host": "sim", "port": 0},
        )
    ).json()

    created = await client.post(
        "/api/webhooks",
        json={
            "name": "Discord",
            "event": "print_finished",
            "url": "https://example.invalid/hook",
            "printer_id": printer["id"],
        },
    )
    assert created.status_code == 201
    webhook = created.json()
    assert webhook["event"] == "print_finished"
    assert webhook["printer_id"] == printer["id"]

    listed = await client.get("/api/webhooks")
    assert [w["id"] for w in listed.json()] == [webhook["id"]]

    patched = await client.patch(
        f"/api/webhooks/{webhook['id']}",
        json={"event": "bed_cold", "bed_cold_threshold": 35},
    )
    assert patched.status_code == 200
    assert patched.json()["event"] == "bed_cold"
    assert patched.json()["bed_cold_threshold"] == 35

    deleted = await client.delete(f"/api/webhooks/{webhook['id']}")
    assert deleted.status_code == 200
    assert (await client.get("/api/webhooks")).json() == []


async def test_webhook_refuse_imprimante_et_etiquette_ensemble(client):
    printer = (
        await client.post(
            "/api/printers",
            json={"name": "k2b", "transport": "simulator", "host": "sim", "port": 0},
        )
    ).json()

    response = await client.post(
        "/api/webhooks",
        json={
            "event": "print_finished",
            "url": "https://example.invalid/hook",
            "printer_id": printer["id"],
            "tag": "atelier",
        },
    )
    assert response.status_code == 400
