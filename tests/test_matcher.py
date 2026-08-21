"""Regles d'attribution des travaux."""
from __future__ import annotations

import pytest

from allbudy.models import GcodeFile, Job, Printer, Spool
from allbudy.queueing.matcher import (
    color_distance,
    evaluate,
    job_requirements,
    match_spools,
    normalize_material,
)


def make_printer(**kwargs) -> Printer:
    defaults = {
        "id": 1,
        "name": "K1",
        "enabled": True,
        "auto_assign": True,
        "nozzle_diameter": 0.4,
        "tags": [],
    }
    return Printer(**{**defaults, "id": kwargs.pop("id", 1), **kwargs})


def make_job(**kwargs) -> Job:
    defaults = {
        "id": 1,
        "name": "test",
        "file_id": 1,
        "color_tolerance": 40,
        "required_tags": [],
        "allowed_printers": [],
    }
    return Job(**{**defaults, **kwargs})


def make_spool(spool_id: int, material="PLA", color="#FF0000", **kwargs) -> Spool:
    return Spool(
        id=spool_id, unit=0, slot=spool_id, material=material, color_hex=color,
        empty=kwargs.pop("empty", False), active=kwargs.pop("active", False), **kwargs
    )


def make_file(meta: dict | None = None) -> GcodeFile:
    return GcodeFile(id=1, filename="x.gcode", stored_name="x.gcode", kind="gcode", meta=meta or {})


# ------------------------------------------------------------------ couleurs
def test_color_distance_identique():
    assert color_distance("#FF0000", "#FF0000") == 0


def test_color_distance_proche_vs_lointaine():
    proche = color_distance("#FF0000", "#FA0505")
    lointaine = color_distance("#FF0000", "#0000FF")
    assert proche < 40 < lointaine


def test_color_distance_illisible():
    assert color_distance("#FF0000", "pas-une-couleur") is None


@pytest.mark.parametrize(
    ("brut", "attendu"),
    [("PLA+", "PLA"), ("pla", "PLA"), ("PLA-CF", "PLACF"), ("Hyper PLA", "PLA"), (None, "")],
)
def test_normalize_material(brut, attendu):
    assert normalize_material(brut) == attendu


# -------------------------------------------------------------- exigences
def test_requirements_depuis_le_fichier():
    file = make_file({"filament_types": ["PLA", "PETG"], "filament_colors": ["#FF0000", "#00FF00"]})
    requirements = job_requirements(make_job(), file)
    assert [(r.material, r.color) for r in requirements] == [
        ("PLA", "#FF0000"),
        ("PETG", "#00FF00"),
    ]


def test_le_travail_prime_sur_le_fichier():
    file = make_file({"filament_types": ["PLA"], "filament_colors": ["#FF0000"]})
    job = make_job(required_material="ABS", required_color="#000000")
    requirements = job_requirements(job, file)
    assert len(requirements) == 1
    assert requirements[0].material == "ABS"
    assert requirements[0].color == "#000000"


# ----------------------------------------------------------- appariement
def test_match_spools_multi_materiaux():
    from allbudy.queueing.matcher import Requirement

    spools = [
        make_spool(1, "PETG", "#00FF00"),
        make_spool(2, "PLA", "#FF0000"),
    ]
    requirements = [Requirement("PLA", "#FF0000"), Requirement("PETG", "#00FF00")]
    ok, used, reason = match_spools(requirements, spools, 40)
    assert ok, reason
    assert sorted(used) == [1, 2]


def test_une_bobine_ne_sert_qu_une_fois():
    from allbudy.queueing.matcher import Requirement

    spools = [make_spool(1, "PLA", "#FF0000")]
    requirements = [Requirement("PLA", "#FF0000"), Requirement("PLA", "#FF0000")]
    ok, _, reason = match_spools(requirements, spools, 40)
    assert not ok
    assert "aucune bobine" in reason


def test_bobine_vide_ignoree():
    from allbudy.queueing.matcher import Requirement

    spools = [make_spool(1, "PLA", "#FF0000", empty=True)]
    ok, _, _ = match_spools([Requirement("PLA", "#FF0000")], spools, 40)
    assert not ok


# ------------------------------------------------------------- evaluation
def _evaluate(job, printer, spools, connected=True, free=True, file=None):
    return evaluate(job, file, printer, spools, connected=connected, free=free)


def test_evaluate_ok():
    result = _evaluate(
        make_job(required_material="PLA", required_color="#FF0000"),
        make_printer(),
        [make_spool(1, "PLA", "#FF0000", active=True)],
    )
    assert result.ok
    # Le filament deja charge fait gagner des points.
    assert result.score > 1.0


def test_evaluate_hors_ligne():
    result = _evaluate(make_job(), make_printer(), [], connected=False)
    assert not result.ok and result.reason == "hors ligne"


def test_evaluate_occupee():
    result = _evaluate(make_job(), make_printer(), [], free=False)
    assert not result.ok and result.reason == "occupee"


def test_evaluate_auto_assign_desactive():
    result = _evaluate(make_job(), make_printer(auto_assign=False), [])
    assert not result.ok


def test_evaluate_buse_incompatible():
    job = make_job(required_nozzle=0.6)
    result = _evaluate(job, make_printer(nozzle_diameter=0.4), [make_spool(1)])
    assert not result.ok and "buse" in result.reason


def test_evaluate_buse_depuis_metadonnees():
    file = make_file({"nozzle_diameter": 0.8})
    result = _evaluate(make_job(), make_printer(nozzle_diameter=0.4), [make_spool(1)], file=file)
    assert not result.ok and "buse" in result.reason


def test_evaluate_etiquettes_manquantes():
    job = make_job(required_tags=["chambre-chauffee"])
    result = _evaluate(job, make_printer(tags=["grande-plaque"]), [make_spool(1)])
    assert not result.ok and "etiquettes" in result.reason


def test_evaluate_etiquettes_presentes():
    job = make_job(required_tags=["ABS"])
    result = _evaluate(job, make_printer(tags=["abs", "autre"]), [make_spool(1)])
    assert result.ok


def test_evaluate_imprimante_non_autorisee():
    job = make_job(allowed_printers=[99])
    result = _evaluate(job, make_printer(id=1), [make_spool(1)])
    assert not result.ok and "autorisee" in result.reason


def test_evaluate_couleur_absente():
    job = make_job(required_material="PLA", required_color="#0000FF")
    result = _evaluate(job, make_printer(), [make_spool(1, "PLA", "#FF0000")])
    assert not result.ok and "aucune bobine" in result.reason


def test_evaluate_couleur_dans_la_tolerance():
    job = make_job(required_material="PLA", required_color="#FF0000", color_tolerance=120)
    result = _evaluate(job, make_printer(), [make_spool(1, "PLA", "#EE1111")])
    assert result.ok


def test_evaluate_sans_contrainte():
    """Un fichier sans metadonnee filament part sur n'importe quelle machine libre."""
    result = _evaluate(make_job(), make_printer(), [], file=make_file({}))
    assert result.ok
