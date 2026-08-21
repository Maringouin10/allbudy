"""Regles d'attribution d'un travail a une imprimante.

Un travail n'est envoye que sur une machine qui a physiquement de quoi
l'imprimer: la bonne buse, les bonnes matieres et les bonnes couleurs chargees
dans le CFS (ou sur le support externe).
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..models import GcodeFile, Job, Printer, Spool

#: Materiaux consideres comme equivalents pour l'attribution.
_MATERIAL_ALIASES = {
    "PLA+": "PLA",
    "PLA PRO": "PLA",
    "HYPER PLA": "PLA",
    "PLA-CF": "PLACF",
    "PETG-CF": "PETGCF",
    "ABS+": "ABS",
    "TPU95A": "TPU",
}


def normalize_material(value: str | None) -> str:
    if not value:
        return ""
    text = str(value).strip().upper().replace("_", " ")
    return _MATERIAL_ALIASES.get(text, text.replace(" ", "").replace("-", ""))


def parse_hex(color: str | None) -> tuple[int, int, int] | None:
    if not color:
        return None
    text = str(color).strip().lstrip("#")
    if len(text) == 8:
        text = text[:6]
    if len(text) != 6:
        return None
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        return None


def color_distance(first: str | None, second: str | None) -> float | None:
    """Distance euclidienne RGB ponderee (approximation de la perception).

    Retourne None si l'une des couleurs est illisible: l'appelant decide alors
    de ne pas contraindre sur la couleur plutot que de rejeter a tort.
    """
    left, right = parse_hex(first), parse_hex(second)
    if left is None or right is None:
        return None
    red_mean = (left[0] + right[0]) / 2
    dr, dg, db = (left[i] - right[i] for i in range(3))
    return (
        (2 + red_mean / 256) * dr * dr + 4 * dg * dg + (2 + (255 - red_mean) / 256) * db * db
    ) ** 0.5


@dataclass(slots=True)
class Requirement:
    """Un filament necessaire au travail."""

    material: str = ""
    color: str | None = None

    def describe(self) -> str:
        return f"{self.material or 'matiere libre'} {self.color or ''}".strip()


@dataclass(slots=True)
class MatchResult:
    ok: bool
    reason: str = ""
    score: float = 0.0
    slots: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "reason": self.reason, "score": self.score, "slots": self.slots}


def job_requirements(job: Job, file: GcodeFile | None) -> list[Requirement]:
    """Filaments necessaires: contraintes explicites du travail, sinon metadonnees.

    Un travail qui force une matiere/couleur ignore ce que dit le fichier: c'est
    l'operateur qui tranche.
    """
    if job.required_material or job.required_color:
        return [
            Requirement(
                material=normalize_material(job.required_material),
                color=job.required_color or None,
            )
        ]

    meta = (file.meta if file else None) or {}
    materials = [normalize_material(m) for m in meta.get("filament_types", [])]
    colors = list(meta.get("filament_colors", []))
    count = max(len(materials), len(colors))
    requirements = [
        Requirement(
            material=materials[i] if i < len(materials) else "",
            color=colors[i] if i < len(colors) else None,
        )
        for i in range(count)
    ]
    return [r for r in requirements if r.material or r.color]


def _spool_satisfies(requirement: Requirement, spool: Spool, tolerance: int) -> bool:
    if spool.empty:
        return False
    if requirement.material and normalize_material(spool.material) != requirement.material:
        return False
    if requirement.color:
        distance = color_distance(requirement.color, spool.color_hex)
        # Couleur illisible cote bobine: on ne bloque pas sur ce critere.
        if distance is not None and distance > tolerance:
            return False
    return True


def match_spools(
    requirements: Iterable[Requirement], spools: list[Spool], tolerance: int
) -> tuple[bool, list[int], str]:
    """Associe chaque besoin a une bobine distincte (glouton).

    Le glouton suffit ici: le nombre d'emplacements est petit (4 par CFS) et les
    besoins sont peu nombreux.
    """
    available = list(spools)
    used: list[int] = []
    for requirement in requirements:
        chosen = None
        # Priorite au filament deja charge jusqu'a la buse: pas de changement.
        for spool in sorted(available, key=lambda s: (not s.active, s.unit, s.slot)):
            if _spool_satisfies(requirement, spool, tolerance):
                chosen = spool
                break
        if chosen is None:
            return False, used, f"aucune bobine pour {requirement.describe()}"
        available.remove(chosen)
        used.append(chosen.id)
    return True, used, ""


def evaluate(
    job: Job,
    file: GcodeFile | None,
    printer: Printer,
    spools: list[Spool],
    *,
    connected: bool,
    free: bool,
) -> MatchResult:
    """Determine si `printer` peut prendre `job` maintenant."""
    if not printer.enabled:
        return MatchResult(False, "imprimante desactivee")
    if not printer.auto_assign:
        return MatchResult(False, "attribution automatique desactivee")
    if not connected:
        return MatchResult(False, "hors ligne")
    if not free:
        return MatchResult(False, "occupee")

    allowed = job.allowed_printers or []
    if allowed and printer.id not in allowed:
        return MatchResult(False, "hors de la liste autorisee")

    required_tags = {str(t).lower() for t in (job.required_tags or [])}
    printer_tags = {str(t).lower() for t in (printer.tags or [])}
    missing = required_tags - printer_tags
    if missing:
        return MatchResult(False, f"etiquettes manquantes: {', '.join(sorted(missing))}")

    nozzle = job.required_nozzle
    if nozzle is None and file:
        nozzle = (file.meta or {}).get("nozzle_diameter")
    if nozzle and abs(float(nozzle) - float(printer.nozzle_diameter)) > 0.01:
        return MatchResult(
            False, f"buse {printer.nozzle_diameter} mm au lieu de {float(nozzle)} mm"
        )

    requirements = job_requirements(job, file)
    if not requirements:
        # Rien d'exigible: on accepte, l'operateur assume le filament en place.
        return MatchResult(True, "aucune contrainte filament", score=1.0)

    if not spools:
        return MatchResult(False, "aucune bobine declaree sur cette machine")

    ok, slots, reason = match_spools(requirements, spools, job.color_tolerance)
    if not ok:
        return MatchResult(False, reason)

    active_ids = {s.id for s in spools if s.active}
    # Favorise les machines ou le filament voulu est deja charge.
    score = 1.0 + sum(1.0 for slot_id in slots if slot_id in active_ids)
    return MatchResult(True, "compatible", score=score, slots=slots)
