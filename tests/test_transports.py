"""Traduction des trames firmware en etat normalise."""
from __future__ import annotations

import pytest

from allbudy.printers.base import (
    STATE_COMPLETE,
    STATE_ERROR,
    STATE_IDLE,
    STATE_PAUSED,
    STATE_PRINTING,
    PrinterConfig,
    PrinterError,
)
from allbudy.printers.creality_lan import CrealityLanTransport
from allbudy.printers.moonraker import MoonrakerTransport
from allbudy.printers.parsing import normalize_color, parse_cfs_payload, parse_creality_box_payload
from allbudy.printers.simulator import SimulatorTransport


def config(**kwargs) -> PrinterConfig:
    base = {"id": 1, "name": "test", "host": "127.0.0.1", "port": 7125}
    return PrinterConfig(**{**base, **kwargs})


# ------------------------------------------------------------------ couleurs
@pytest.mark.parametrize(
    ("brut", "attendu"),
    [
        ("#ff0000", "#FF0000"),
        ("00FF00", "#00FF00"),
        ("0000FFAA", "#0000FF"),
        (0xFF8800, "#FF8800"),
        ([255, 0, 0], "#FF0000"),
        ("nawak", None),
        (None, None),
    ],
)
def test_normalize_color(brut, attendu):
    assert normalize_color(brut) == attendu


# ------------------------------------------------------------------- CFS
def test_parse_cfs_liste_de_boitiers():
    payload = {
        "boxs": [
            {
                "id": 0,
                "materials": [
                    {"id": 0, "type": "PLA", "color": "FF0000", "percent": 80},
                    {"id": 1, "type": "PETG", "color": "#00FF00", "percent": 0},
                ],
            },
            {"id": 1, "materials": [{"id": 0, "type": "ABS", "color": "000000", "percent": 55}]},
        ],
        "cur_id": 0,
    }
    spools = parse_cfs_payload(payload)
    assert len(spools) == 3
    assert (spools[0].unit, spools[0].slot, spools[0].material) == (0, 0, "PLA")
    assert spools[0].color_hex == "#FF0000"
    assert spools[0].active is True
    # Un emplacement a 0 % est considere comme vide: la file ne l'utilisera pas.
    assert spools[1].empty is True
    assert spools[2].unit == 1 and spools[2].material == "ABS"


def test_parse_cfs_liste_plate():
    spools = parse_cfs_payload({"materials": [{"type": "PLA", "color": "112233"}]})
    assert len(spools) == 1
    assert spools[0].color_hex == "#112233"


def test_parse_cfs_charge_utile_invalide():
    assert parse_cfs_payload(None) == []
    assert parse_cfs_payload({"boxs": "pas une liste"}) == []
    assert parse_cfs_payload({"boxs": [{"materials": ["chaine inattendue"]}]}) == []


# ------------------------------------------------- CFS Creality (box K2/K1 Max)
#: Charge utile reelle d'un K2 Plus (journal utilisateur), tronquee des champs
#: sans effet sur le parsing (uuid, measuring_wheel, sn...). Boitier T1 seul
#: present; T2-T4 sont des emplacements physiques absents (state "None").
_REAL_BOX_PAYLOAD = {
    "filament": 1,
    "state": "connect",
    "auto_refill": 1,
    "enable": 1,
    "filament_useup": 0,
    "same_material": [
        ["101001", "0C12E1F", ["T1B", "T1D"], "PLA"],
        ["101001", "0FFFFFF", ["T1C"], "PLA"],
    ],
    "T1": {
        "state": "connect",
        "filament": "B",
        "vender": [
            "none",
            "CC0240276A21010010C12E1F0330000001000000",
            "9C6250276A21010010FFFFFF0330000001000000",
            "BA1250276A21010010C12E1F0330000001000000",
        ],
        "remain_len": ["0", "9", "96", "54"],
        "color_value": ["0C12E1F", "0C12E1F", "0FFFFFF", "0C12E1F"],
        "material_type": ["101001", "101001", "101001", "101001"],
    },
    "T2": {"state": "None", "filament": "None", "vender": ["-1", "-1", "-1", "-1"]},
    "T3": {"state": "None", "filament": "None", "vender": ["-1", "-1", "-1", "-1"]},
    "T4": {"state": "None", "filament": "None", "vender": ["-1", "-1", "-1", "-1"]},
}


def test_parse_creality_box_payload_reel():
    """Rejoue la charge utile exacte d'un K2 Plus rapportee par un utilisateur."""
    spools = parse_creality_box_payload(_REAL_BOX_PAYLOAD)
    # Seul T1 est physiquement present: 4 emplacements, pas 16.
    assert len(spools) == 4
    assert {s.unit for s in spools} == {1}

    by_slot = {s.slot: s for s in spools}
    assert by_slot[0].empty is True and by_slot[0].material == "vide"

    assert by_slot[1].material == "PLA"
    assert by_slot[1].color_hex == "#C12E1F"
    assert by_slot[1].remaining_pct == 9.0
    assert by_slot[1].empty is False
    # Boitier actif (filament=1) et lettre active (B) du boitier T1: slot B seul actif.
    assert by_slot[1].active is True

    assert by_slot[2].material == "PLA"
    assert by_slot[2].color_hex == "#FFFFFF"
    assert by_slot[2].remaining_pct == 96.0
    assert by_slot[2].active is False

    assert by_slot[3].material == "PLA"
    assert by_slot[3].color_hex == "#C12E1F"
    assert by_slot[3].remaining_pct == 54.0
    assert by_slot[3].active is False


def test_parse_creality_box_payload_boitier_sans_same_material():
    """Un emplacement occupe sans correspondance dans same_material reste visible.

    Un boitier compte toujours 4 emplacements physiques (A-D); seul le
    premier est documente ici, les trois autres sont donc vides par defaut.
    """
    payload = {
        "filament": 1,
        "T1": {
            "state": "connect",
            "filament": "None",
            "vender": ["TAG123"],
            "remain_len": ["42"],
            "color_value": ["0AABBCC"],
            "material_type": ["999999"],
        },
    }
    spools = parse_creality_box_payload(payload)
    assert len(spools) == 4
    slot_a = next(s for s in spools if s.slot == 0)
    assert slot_a.empty is False
    assert slot_a.material == "materiau 999999"
    assert slot_a.color_hex == "#AABBCC"
    assert all(s.empty for s in spools if s.slot != 0)


def test_parse_creality_box_payload_ignore_les_boitiers_absents():
    payload = {"T1": {"state": "None"}, "T2": {"state": "None"}}
    assert parse_creality_box_payload(payload) == []


def test_parse_creality_box_payload_charge_utile_invalide():
    assert parse_creality_box_payload(None) == []
    assert parse_creality_box_payload({}) == []
    assert parse_creality_box_payload({"T1": "pas un dict"}) == []


# ------------------------------------------------------------- Moonraker
def moonraker_with_objects(objects: dict) -> MoonrakerTransport:
    transport = MoonrakerTransport(config())
    transport._objects = objects
    return transport


def test_moonraker_etat_impression():
    transport = moonraker_with_objects({
        "print_stats": {
            "state": "printing",
            "filename": "cube.gcode",
            "print_duration": 600.0,
            "info": {"current_layer": 40, "total_layer": 200},
        },
        "display_status": {"progress": 0.25},
        "extruder": {"temperature": 219.7, "target": 220.0},
        "heater_bed": {"temperature": 59.4, "target": 60.0},
        "fan": {"speed": 1.0},
        "toolhead": {"position": [110.0, 95.5, 8.0, 0.0]},
        "gcode_move": {"speed_factor": 1.0, "extrude_factor": 0.98},
        "webhooks": {"state": "ready"},
    })
    status = transport._build_status()
    assert status.state == STATE_PRINTING
    assert status.progress == 25.0
    assert status.nozzle_temp == 219.7
    assert status.bed_target == 60.0
    assert status.current_layer == 40 and status.total_layers == 200
    assert status.part_fan == 100.0
    assert status.position == {"x": 110.0, "y": 95.5, "z": 8.0}
    assert status.flow_factor == 98.0
    # 600 s pour 25 %: il reste environ trois fois plus.
    assert status.remaining_time == pytest.approx(1800, rel=0.02)


@pytest.mark.parametrize(
    ("klipper", "attendu"),
    [
        ("standby", STATE_IDLE),
        ("printing", STATE_PRINTING),
        ("paused", STATE_PAUSED),
        ("complete", STATE_COMPLETE),
        ("error", STATE_ERROR),
        ("cancelled", STATE_IDLE),
    ],
)
def test_moonraker_correspondance_etats(klipper, attendu):
    transport = moonraker_with_objects({"print_stats": {"state": klipper}})
    assert transport._build_status().state == attendu


def test_moonraker_klipper_arrete():
    transport = moonraker_with_objects({
        "print_stats": {"state": "standby"},
        "webhooks": {"state": "shutdown", "state_message": "MCU 'mcu' shutdown"},
    })
    status = transport._build_status()
    assert status.state == STATE_ERROR
    assert "shutdown" in status.state_message


def test_moonraker_objets_absents_ne_cassent_rien():
    """Un firmware minimaliste ne doit pas faire echouer la lecture d'etat."""
    status = moonraker_with_objects({})._build_status()
    assert status.online is True
    assert status.nozzle_temp == 0.0
    assert status.chamber_temp is None


def test_moonraker_chambre_et_ventilateurs_detectes():
    transport = MoonrakerTransport(config())
    transport._available = [
        "extruder",
        "heater_generic chamber_heater",
        "fan_generic auxiliary_cooling_fan",
        "output_pin caselight",
        "box",
    ]
    transport._detect_objects()
    assert transport._chamber_key == "heater_generic chamber_heater"
    assert transport._aux_fan_key == "fan_generic auxiliary_cooling_fan"
    assert transport._light_key == "output_pin caselight"
    assert transport._cfs_key == "box"
    assert transport.config.has_cfs is True

    transport._objects = {
        "heater_generic chamber_heater": {"temperature": 44.2, "target": 50.0},
        "fan_generic auxiliary_cooling_fan": {"speed": 0.6},
        "output_pin caselight": {"value": 1.0},
        "box": {"materials": [{"type": "PLA", "color": "FFFFFF"}]},
    }
    status = transport._build_status()
    assert status.chamber_temp == 44.2 and status.chamber_target == 50.0
    assert status.aux_fan == 60.0
    assert status.light_on is True
    assert len(status.spools) == 1


def test_moonraker_cfs_instance_nommee(caplog):
    """Une instance Klipper nommee ('cfs cfs0') doit matcher via le type seul."""
    transport = MoonrakerTransport(config())
    transport._available = ["extruder", "cfs cfs0"]
    with caplog.at_level("INFO"):
        transport._detect_objects()
    assert transport._cfs_key == "cfs cfs0"
    assert transport.config.has_cfs is True
    assert any("CFS detecte via l'objet 'cfs cfs0'" in message for message in caplog.messages)


def test_moonraker_cfs_non_detecte_journalise(caplog):
    """Sans objet correspondant, le journal liste tous les objets exposes."""
    transport = MoonrakerTransport(config())
    transport._available = ["extruder", "heater_bed"]
    with caplog.at_level("INFO"):
        transport._detect_objects()
    assert transport._cfs_key is None
    assert any("NON detecte" in message for message in caplog.messages)
    assert any("extruder" in message and "heater_bed" in message for message in caplog.messages)


def test_moonraker_cfs_objet_trouve_mais_illisible_journalise_le_brut(caplog):
    """Objet detecte mais structure non reconnue: le contenu brut est journalise."""
    transport = MoonrakerTransport(config())
    transport._available = ["cfs"]
    transport._detect_objects()
    assert transport._cfs_key == "cfs"
    transport._objects = {"cfs": {"unexpected_shape": True}}
    with caplog.at_level("INFO"):
        spools = transport._parse_cfs()
    assert spools == []
    assert any(
        "aucun emplacement reconnu" in message and "unexpected_shape" in message
        for message in caplog.messages
    )


def test_moonraker_parse_cfs_via_le_format_box_reel():
    """Bout en bout: l'objet 'box' d'un vrai K2 Plus doit produire les 4 emplacements."""
    transport = MoonrakerTransport(config())
    transport._available = ["box"]
    transport._detect_objects()
    assert transport._cfs_key == "box"
    transport._objects = {"box": _REAL_BOX_PAYLOAD}
    spools = transport._parse_cfs()
    assert len(spools) == 4
    assert any(s.material == "PLA" and not s.empty for s in spools)


def test_moonraker_detection_chambre_par_heuristique():
    transport = MoonrakerTransport(config())
    transport._available = ["temperature_sensor Chamber_XYZ"]
    transport._detect_objects()
    assert transport._chamber_key == "temperature_sensor Chamber_XYZ"


# ----------------------------------------------------------- LAN Creality
class _FakeSocket:
    """Tient lieu de WebSocket ouvert: refresh() verifie sa presence."""

    closed = False


def lan_with_payload(payload: dict) -> CrealityLanTransport:
    transport = CrealityLanTransport(config(port=9999))
    transport._payload = payload
    transport._ws = _FakeSocket()
    transport._connected.set()
    return transport


async def test_lan_etat_impression():
    transport = lan_with_payload({
        "nozzleTemp": "218",
        "targetNozzleTemp": "220",
        "bedTemp0": "59",
        "targetBedTemp0": "60",
        "boxTemp": "38",
        "printProgress": 42,
        "layer": 84,
        "TotalLayer": 200,
        "printJobTime": 1800,
        "printLeftTime": 2400,
        "printFileName": "/usr/data/printer_data/gcodes/piece.gcode",
        "state": 1,
        "lightSw": 1,
        "fan": 1,
        "model": "K1 Max",
    })
    status = await transport.refresh()
    assert status.state == STATE_PRINTING
    assert status.nozzle_temp == 218.0 and status.nozzle_target == 220.0
    assert status.chamber_temp == 38.0
    assert status.progress == 42.0
    assert status.current_layer == 84 and status.total_layers == 200
    assert status.remaining_time == 2400
    # Le nom de fichier est nettoye de son chemin machine.
    assert status.filename == "piece.gcode"
    assert status.light_on is True
    assert status.part_fan == 100.0
    assert status.model == "K1 Max"


async def test_lan_pause_prime_sur_letat():
    transport = lan_with_payload({"state": 1, "pause": 1, "printProgress": 50})
    assert (await transport.refresh()).state == STATE_PAUSED


async def test_lan_erreur_firmware():
    transport = lan_with_payload({"state": 1, "err": 213})
    status = await transport.refresh()
    assert status.state == STATE_ERROR
    assert "213" in status.state_message


async def test_lan_alias_de_champs():
    """Les noms de champs varient selon les firmwares."""
    transport = lan_with_payload({"curNozzleTemp": "205", "curBedTemp": "55", "dProgress": 10})
    status = await transport.refresh()
    assert status.nozzle_temp == 205.0
    assert status.bed_temp == 55.0
    assert status.progress == 10.0


async def test_lan_refuse_si_deconnecte():
    transport = CrealityLanTransport(config(port=9999))
    with pytest.raises(PrinterError):
        await transport.refresh()


def test_lan_liste_de_fichiers():
    transport = lan_with_payload({})
    transport._ingest_file_list({"file": [
        "/usr/data/printer_data/gcodes/a.gcode",
        {"path": "/usr/data/printer_data/gcodes/b.gcode", "size": 1234},
    ]})
    files = transport._files
    assert [f.name for f in files] == ["a.gcode", "b.gcode"]
    assert files[1].size == 1234


def test_lan_cfs_encapsule_en_json():
    """Certains firmwares serialisent l'etat du CFS dans une chaine."""
    import json

    transport = lan_with_payload(
        {"boxsInfo": json.dumps({"boxs": [{"id": 0, "materials": [
            {"id": 0, "type": "PLA", "color": "FF0000", "percent": 90}]}]})}
    )
    spools = transport._parse_spools()
    assert len(spools) == 1 and spools[0].material == "PLA"


def test_lan_champ_present_mais_illisible_journalise_le_brut(caplog):
    """Un champ evoquant le CFS mais de structure inconnue: log du contenu brut."""
    transport = lan_with_payload({"cfsInfo": {"unexpected_shape": True}})
    with caplog.at_level("INFO"):
        spools = transport._parse_spools()
    assert spools == []
    assert any(
        "aucun emplacement reconnu" in message and "unexpected_shape" in message
        for message in caplog.messages
    )


def test_lan_cfs_non_detecte_journalise_les_champs(caplog):
    transport = lan_with_payload({"nozzleTemp": "210"})
    with caplog.at_level("INFO"):
        spools = transport._parse_spools()
    assert spools == []
    assert any("nozzleTemp" in message for message in caplog.messages)


# ------------------------------------------------------------- simulateur
async def test_simulateur_cycle(tmp_path):
    transport = SimulatorTransport(config(port=0))
    await transport.connect()
    assert (await transport.refresh()).state == STATE_IDLE
    assert len(transport.status.spools) == 4

    source = tmp_path / "piece.gcode"
    source.write_text("G28\n", encoding="utf-8")
    transport.set_print_duration(1)
    await transport.upload(source, "piece.gcode")
    await transport.start_print("piece.gcode")
    assert (await transport.refresh()).state == STATE_PRINTING

    await transport.pause()
    assert (await transport.refresh()).state == STATE_PAUSED
    await transport.resume()
    await transport.cancel()
    assert (await transport.refresh()).state == STATE_IDLE


async def test_simulateur_refuse_fichier_absent():
    transport = SimulatorTransport(config(port=0))
    await transport.connect()
    with pytest.raises(PrinterError):
        await transport.start_print("inexistant.gcode")


async def test_simulateur_refuse_double_impression(tmp_path):
    transport = SimulatorTransport(config(port=0))
    await transport.connect()
    source = tmp_path / "a.gcode"
    source.write_text("G28\n", encoding="utf-8")
    await transport.upload(source, "a.gcode", start=True)
    with pytest.raises(PrinterError):
        await transport.start_print("a.gcode")


# ------------------------------------------------------------ ventilateurs
def test_moonraker_detaille_les_ventilateurs():
    """Vitesse, regime, et distinction pilotable / asservi au firmware."""
    transport = MoonrakerTransport(config())
    transport._available = [
        "fan",
        "fan_generic auxiliary_cooling_fan",
        "fan_generic chamber_circulation_fan",
        "heater_fan hotend_fan",
    ]
    transport._detect_objects()
    assert transport._extra_fans == ["heater_fan hotend_fan"]

    transport._objects = {
        "fan": {"speed": 1.0, "rpm": 5100},
        "fan_generic auxiliary_cooling_fan": {"speed": 0.6},
        "fan_generic chamber_circulation_fan": {"speed": 0.0},
        "heater_fan hotend_fan": {"speed": 1.0, "rpm": 7000},
    }
    fans = transport._build_status().fans
    assert [f.label for f in fans] == ["Piece", "Auxiliaire", "Chambre", "Hotend fan"]

    part = fans[0]
    assert part.speed == 100.0 and part.rpm == 5100 and part.controllable is True
    # Sans capteur de regime, le champ reste vide plutot que faux.
    assert fans[1].speed == 60.0 and fans[1].rpm is None
    # Le ventilateur de tete est asservi au firmware: observable, pas pilotable.
    assert fans[3].key is None and fans[3].controllable is False


def test_moonraker_sans_ventilateur():
    assert moonraker_with_objects({})._build_status().fans == []


async def test_lan_liste_les_ventilateurs():
    transport = lan_with_payload({"fan": 1, "fanAuxiliary": 0, "fanCase": 1})
    fans = (await transport.refresh()).fans
    assert [(f.key, f.speed) for f in fans] == [
        ("part", 100.0),
        ("aux", 0.0),
        ("chamber", 100.0),
    ]
    # Le protocole LAN n'expose aucun regime.
    assert all(f.rpm is None for f in fans)


async def test_simulateur_ventilateurs():
    transport = SimulatorTransport(config(port=0))
    await transport.connect()
    idle = await transport.refresh()
    assert [f.label for f in idle.fans] == ["Piece", "Auxiliaire", "Chambre", "Tete"]
    assert all(f.speed == 0 for f in idle.fans)
