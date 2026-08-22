"""Lecture des projets 3MF (Creality Print, Orca Slicer, PrusaSlicer).

Un 3MF est une archive ZIP. Deux cas se presentent:

* projet non tranche: on lit ses reglages pour connaitre materiaux et couleurs,
  mais il n'est PAS imprimable tel quel (il doit passer par le trancheur);
* 3MF tranche (contient un `.gcode`): imprimable, on reutilise le parseur
  G-code pour les metadonnees fines.
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from .gcode_meta import GcodeMetadata, parse_gcode_text

#: Emplacements connus des reglages de projet.
_CONFIG_NAMES = (
    "Metadata/project_settings.config",
    "Metadata/Slic3r_PE.config",
    "Metadata/slic3r.config",
)

#: Emplacements connus des miniatures, du plus grand au plus petit.
_THUMB_NAMES = (
    "Metadata/plate_1_large.png",
    "Metadata/plate_1.png",
    "Metadata/thumbnail.png",
    "Thumbnails/thumbnail.png",
    "Metadata/top_1.png",
)

_MAX_EMBEDDED_GCODE = 64 * 1024 * 1024


def _normalize_color(value: str) -> str | None:
    text = str(value).strip().lstrip("#")
    if len(text) == 8:
        text = text[:6]
    if len(text) != 6:
        return None
    try:
        int(text, 16)
    except ValueError:
        return None
    return f"#{text.upper()}"


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [p.strip() for p in value.replace(";", ",").split(",") if p.strip()]
    return []


def _parse_json_config(raw: bytes, meta: GcodeMetadata) -> None:
    """Reglages Orca/Creality Print (JSON)."""
    try:
        config = json.loads(raw.decode("utf-8", "replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    if not isinstance(config, dict):
        return

    meta.filament_types = meta.filament_types or [
        t.upper() for t in _as_list(config.get("filament_type"))
    ]
    colors = [
        c for c in (_normalize_color(v) for v in _as_list(config.get("filament_colour"))) if c
    ]
    meta.filament_colors = meta.filament_colors or colors

    nozzles = _as_list(config.get("nozzle_diameter"))
    if nozzles and meta.nozzle_diameter is None:
        try:
            meta.nozzle_diameter = float(nozzles[0])
        except ValueError:
            pass
    for key, attr in (
        ("layer_height", "layer_height"),
        ("initial_layer_print_height", "first_layer_height"),
    ):
        values = _as_list(config.get(key))
        if values and getattr(meta, attr) is None:
            try:
                setattr(meta, attr, float(values[0]))
            except ValueError:
                pass
    printer = config.get("printer_settings_id") or config.get("printer_model")
    if printer and not meta.printer_model:
        meta.printer_model = str(printer)
    if not meta.slicer:
        meta.slicer = str(config.get("from") or "Creality Print / Orca")


def _parse_ini_config(raw: bytes, meta: GcodeMetadata) -> None:
    """Reglages PrusaSlicer (`cle = valeur`), reutilise le parseur G-code."""
    text = "\n".join(
        f"; {line}" for line in raw.decode("utf-8", "replace").splitlines() if "=" in line
    )
    parsed = parse_gcode_text(text)
    for key, value in parsed.__dict__.items():
        if not getattr(meta, key) and value:
            setattr(meta, key, value)


def _count_build_items(raw: bytes) -> int | None:
    """Nombre de pieces placees sur le plateau.

    Dans un 3MF, `<build>` liste un `<item>` par instance posee: c'est ce qui
    correspond au nombre de pieces imprimees, pas le nombre de maillages.
    """
    try:
        text = raw.decode("utf-8", "replace")
    except UnicodeDecodeError:  # pragma: no cover - decode ne leve pas en "replace"
        return None
    build = re.search(r"<build\b.*?</build>", text, re.S | re.I)
    if not build:
        return None
    count = len(re.findall(r"<item\b", build.group(), re.I))
    return count or None


def extract_3mf_metadata(path: Path) -> tuple[GcodeMetadata, bytes | None]:
    meta = GcodeMetadata(printable=False, slicer=None)
    thumbnail: bytes | None = None

    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())

            # Un 3MF tranche embarque le G-code: c'est la source la plus fiable.
            gcode_names = [n for n in names if n.lower().endswith((".gcode", ".gco", ".g"))]
            if gcode_names:
                gcode_name = sorted(gcode_names)[0]
                info = archive.getinfo(gcode_name)
                if info.file_size <= _MAX_EMBEDDED_GCODE:
                    with archive.open(gcode_name) as handle:
                        text = handle.read().decode("utf-8", "replace")
                    meta = parse_gcode_text(text)
                    meta.printable = True

            for name in _CONFIG_NAMES:
                if name in names:
                    raw = archive.read(name)
                    if name.endswith(".config") and raw.lstrip()[:1] == b"{":
                        _parse_json_config(raw, meta)
                    else:
                        _parse_ini_config(raw, meta)
                    break

            # Le modele donne le nombre de pieces quand le G-code ne le dit pas.
            if meta.object_count is None and "3D/3dmodel.model" in names:
                meta.object_count = _count_build_items(archive.read("3D/3dmodel.model"))

            for name in _THUMB_NAMES:
                if name in names:
                    thumbnail = archive.read(name)
                    break
            if thumbnail is None:
                pngs = sorted(n for n in names if n.lower().endswith(".png"))
                if pngs:
                    thumbnail = archive.read(pngs[0])
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise ValueError(f"Fichier 3MF illisible: {exc}") from exc

    return meta, thumbnail
