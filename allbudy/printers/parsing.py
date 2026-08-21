"""Helpers de lecture tolerante des charges utiles firmware.

Les firmwares Creality changent de noms de champs entre versions et entre
modeles: tout ce qui est lu ici doit degrader proprement plutot que lever.
"""
from __future__ import annotations

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


def parse_slots(slots: Any, unit: int) -> list[SpoolState]:
    out: list[SpoolState] = []
    if not isinstance(slots, list):
        return out
    for slot_index, slot in enumerate(slots):
        if not isinstance(slot, dict):
            continue
        index = slot.get("id", slot.get("slot", slot.get("index", slot_index)))
        material = str(
            slot.get("type") or slot.get("material") or slot.get("filament_type") or ""
        ).strip()
        color = normalize_color(
            slot.get("color") or slot.get("rgb") or slot.get("colour") or slot.get("color_hex")
        )
        percent = slot.get("percent", slot.get("remain", slot.get("remaining")))
        state = str(slot.get("state", "")).lower()
        empty = (
            not material
            or material.lower() in ("empty", "none", "unknown")
            or state in ("empty", "none")
            or (isinstance(percent, (int, float)) and percent <= 0)
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

    Accepte soit une liste de boitiers contenant des emplacements, soit une
    liste plate d'emplacements.
    """
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
                if isinstance(unit.get(key), list):
                    slots = unit[key]
                    break
            unit_id = unit.get("id", unit.get("index", unit_index))
            spools.extend(parse_slots(slots, as_int(unit_id, unit_index)))
    else:
        for key in _SLOT_KEYS:
            if isinstance(payload.get(key), list):
                spools.extend(parse_slots(payload[key], 0))
                break

    active_index = payload.get("cur_id", payload.get("active_slot", payload.get("curId")))
    if isinstance(active_index, (int, float)) and int(active_index) >= 0:
        for spool in spools:
            if spool.slot == int(active_index):
                spool.active = True
    return spools
