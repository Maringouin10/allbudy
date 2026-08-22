"""Helpers de lecture tolerante des charges utiles firmware.

Les firmwares Creality changent de noms de champs entre versions et entre
modeles: tout ce qui est lu ici doit degrader proprement plutot que lever.
"""
from __future__ import annotations

import re
from typing import Any

from .base import SpoolState


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    return int(as_float(value, default))


def normalize_color(value: Any) -> str | None:
    """Accepte '#RRGGBB', 'RRGGBB', 'RRGGBBAA', '(255,0,0)' ou un entier."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return f"#{value & 0xFFFFFF:06X}"
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            r, g, b = (max(0, min(255, int(c))) for c in value[:3])
        except (TypeError, ValueError):
            return None
        return f"#{r:02X}{g:02X}{b:02X}"
    text = str(value).strip().lstrip("#")
    if len(text) == 8:  # RRGGBBAA
        text = text[:6]
    if len(text) == 6:
        try:
            int(text, 16)
        except ValueError:
            return None
        return f"#{text.upper()}"
    return None


_SLOT_KEYS = ("materials", "slots", "filaments", "material", "spools")
_UNIT_KEYS = ("boxs", "boxes", "units", "hubs", "cfs")


def _as_entries(value: Any) -> list[tuple[Any, Any]]:
    """Normalise une liste ou un dictionnaire d'emplacements en (cle, valeur).

    Certains firmwares renvoient les emplacements sous forme de dictionnaire
    indexe par numero de slot (`{"0": {...}, "1": {...}}`) plutot qu'en liste.
    """
    if isinstance(value, list):
        return list(enumerate(value))
    if isinstance(value, dict):
        return list(value.items())
    return []


def parse_slots(slots: Any, unit: int) -> list[SpoolState]:
    out: list[SpoolState] = []
    entries = _as_entries(slots)
    for slot_index, slot in entries:
        if not isinstance(slot, dict):
            continue
        index = slot.get("id", slot.get("slot", slot.get("index", slot_index)))
        material = str(
            slot.get("type")
            or slot.get("material")
            or slot.get("filament_type")
            or slot.get("filament")
            or ""
        ).strip()
        color = normalize_color(
            slot.get("color")
            or slot.get("rgb")
            or slot.get("colour")
            or slot.get("color_hex")
            or slot.get("filament_color")
            or slot.get("hex")
        )
        percent = next(
            (
                slot[key]
                for key in ("percent", "remain", "remaining", "percentage", "remain_percent")
                if key in slot
            ),
            None,
        )
        state = str(slot.get("state", "")).lower()
        # Signal booleen explicite de presence de filament, quand fourni.
        # `is_empty` a une polarite opposee a `has_filament`/`loaded`.
        empty_flag = (
            slot.get("has_filament") is False
            or slot.get("loaded") is False
            or slot.get("is_empty") is True
        )
        empty = (
            not material
            or material.lower() in ("empty", "none", "unknown")
            or state in ("empty", "none")
            or (isinstance(percent, (int, float)) and percent <= 0)
            or empty_flag
        )
        out.append(
            SpoolState(
                unit=unit,
                slot=as_int(index, slot_index),
                material=material or "vide",
                color_hex=color or "#7f8c8d",
                vendor=slot.get("vendor") or slot.get("brand"),
                remaining_pct=float(percent) if isinstance(percent, (int, float)) else None,
                active=bool(slot.get("active", False)),
                empty=bool(empty),
            )
        )
    return out


def parse_cfs_payload(payload: Any) -> list[SpoolState]:
    """Normalise l'etat du CFS (Creality Filament System).

    Accepte une liste de boitiers contenant des emplacements, une liste
    plate d'emplacements, ou directement une liste/dictionnaire d'emplacements
    en entree (quand l'objet Klipper ne les encapsule sous aucune cle).
    """
    if isinstance(payload, list):
        return parse_slots(payload, 0)
    if not isinstance(payload, dict):
        return []

    units: list[Any] = []
    for key in _UNIT_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            units = value
            break

    spools: list[SpoolState] = []
    if units:
        for unit_index, unit in enumerate(units):
            if not isinstance(unit, dict):
                continue
            slots: Any = None
            for key in _SLOT_KEYS:
                if key in unit and isinstance(unit[key], (list, dict)):
                    slots = unit[key]
                    break
            unit_id = unit.get("id", unit.get("index", unit_index))
            spools.extend(parse_slots(slots, as_int(unit_id, unit_index)))
    else:
        for key in _SLOT_KEYS:
            if key in payload and isinstance(payload[key], (list, dict)):
                spools.extend(parse_slots(payload[key], 0))
                break

    active_index = payload.get("cur_id", payload.get("active_slot", payload.get("curId")))
    if isinstance(active_index, (int, float)) and int(active_index) >= 0:
        for spool in spools:
            if spool.slot == int(active_index):
                spool.active = True
    return spools


# ---------------------------------------------------------------- box K2/K1 Max
#: Un boitier physique du CFS Creality (K1 Max, K2 Plus) apparait comme "T1".."T4"
#: dans l'objet Klipper "box"; chacun porte jusqu'a 4 emplacements A-D.
_BOX_UNIT_RE = re.compile(r"^T(\d+)$")
_BOX_SLOT_LETTERS = "ABCD"


def _box_color(value: Any) -> str | None:
    """Les couleurs du CFS Creality sont vues sur 7 caracteres hexadecimaux

    (ex: '0C12E1F'): le premier chiffre n'est pas une composante RGB, les
    6 derniers le sont ('0C12E1F' -> couleur 'C12E1F'). Observe sur un K2
    Plus reel, non documente ailleurs.
    """
    text = str(value).strip()
    if len(text) == 7:
        text = text[1:]
    return normalize_color(text)


def parse_creality_box_payload(payload: Any) -> list[SpoolState]:
    """Format de l'objet Klipper "box" (CFS des K1 Max / K2 Plus).

    Reconstruit par observation d'une charge utile reelle (aucune
    documentation publique n'existe). Forme generale::

        {
            "filament": 1,              # boitier actuellement actif (index 1..4)
            "state": "connect",
            "same_material": [          # regroupement + nom humain des emplacements
                ["101001", "0C12E1F", ["T1B", "T1D"], "PLA"],
                ...
            ],
            "T1": {                     # un boitier physique par cle "Tn"
                "state": "connect",     # "None" = boitier absent, a ignorer
                "filament": "B",        # lettre de l'emplacement charge dans ce boitier
                "vender": ["none", "<tag RFID>", ...],       # 4 entrees, A a D
                "remain_len": ["0", "9", "96", "54"],        # pourcentage restant
                "color_value": ["0C12E1F", ...],
                "material_type": ["101001", ...],
            },
            "T2": {"state": "None", ...},   # boitier absent
            ...
        }

    Le code materiau brut ("101001") n'est documente nulle part, mais la
    machine fournit elle-meme son nom humain ("PLA") pour chaque emplacement
    non vide via "same_material": on construit la correspondance a partir de
    la, plutot que de deviner une table de codes.
    """
    if not isinstance(payload, dict):
        return []

    slot_material_names: dict[str, str] = {}
    for group in payload.get("same_material") or []:
        if not isinstance(group, (list, tuple)) or len(group) < 4:
            continue
        name, slot_ids = group[3], group[2]
        if isinstance(name, str) and name and isinstance(slot_ids, list):
            for slot_id in slot_ids:
                slot_material_names[str(slot_id)] = name

    active_box = payload.get("filament")

    spools: list[SpoolState] = []
    for key, box in payload.items():
        match = _BOX_UNIT_RE.match(str(key))
        if not match or not isinstance(box, dict):
            continue
        if str(box.get("state")) != "connect":
            continue  # boitier non present physiquement

        unit = int(match.group(1))
        vendor_tags = box.get("vender") or []
        remaining = box.get("remain_len") or []
        colors = box.get("color_value") or []
        materials = box.get("material_type") or []
        active_letter = box.get("filament")

        for index, letter in enumerate(_BOX_SLOT_LETTERS):
            tag = vendor_tags[index] if index < len(vendor_tags) else None
            percent = as_float(remaining[index], -1) if index < len(remaining) else -1
            empty = not tag or str(tag).lower() == "none" or percent <= 0

            slot_id = f"{key}{letter}"
            name = slot_material_names.get(slot_id)
            code = str(materials[index]) if index < len(materials) else None
            material = "vide" if empty else name or (f"materiau {code}" if code else "inconnu")

            color = _box_color(colors[index]) if index < len(colors) else None

            spools.append(
                SpoolState(
                    unit=unit,
                    slot=index,
                    material=material,
                    color_hex=color or "#7f8c8d",
                    remaining_pct=percent if percent >= 0 else None,
                    active=(
                        not empty
                        and active_letter == letter
                        and active_box is not None
                        and str(active_box) == str(unit)
                    ),
                    empty=empty,
                )
            )
    return spools
