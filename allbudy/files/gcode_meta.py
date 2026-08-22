"""Extraction des metadonnees et miniatures d'un fichier G-code.

Couvre les commentaires emis par Creality Print, Orca Slicer, PrusaSlicer et
Cura: ce sont les trancheurs utilisables avec des machines Creality. Les
metadonnees sont placees en tete par Cura et en pied par les trancheurs
derives de PrusaSlicer, donc on lit les deux extremites du fichier.
"""
from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Quantite lue a chaque extremite du fichier (les metadonnees y tiennent large).
HEAD_BYTES = 128 * 1024
TAIL_BYTES = 256 * 1024

_DURATION_RE = re.compile(
    r"(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?", re.I
)
_THUMB_BEGIN_RE = re.compile(r";\s*thumbnail(?:_QOI|_JPG)?\s+begin\s+(\d+)[x ](\d+)\s+(\d+)", re.I)
_THUMB_END_RE = re.compile(r";\s*thumbnail(?:_QOI|_JPG)?\s+end", re.I)
#: Miniatures Creality Print: une seule ligne base64 (`;gimage:` = grande).
_CREALITY_THUMB_RE = re.compile(r";\s*(gimage|simage)\s*:\s*([A-Za-z0-9+/=]{64,})", re.I)

_MAGIC = {b"\x89PNG": ".png", b"\xff\xd8\xff": ".jpg", b"qoif": ".qoi"}

#: Marqueurs d'objets, par ordre de fiabilite decroissante.
#: `EXCLUDE_OBJECT_DEFINE` (Klipper) est emis en tete, une ligne par piece.
_EXCLUDE_OBJECT_RE = re.compile(r"^\s*EXCLUDE_OBJECT_DEFINE\s+NAME=(\S+)", re.I | re.M)
#: `M486 S<n>` (Marlin/PrusaSlicer): l'index le plus haut donne le nombre de pieces.
_M486_RE = re.compile(r"^\s*M486\s+S(-?\d+)", re.I | re.M)
#: Commentaires nommant l'objet en cours, emis par Orca/PrusaSlicer et Cura.
_PRINTING_OBJECT_RE = re.compile(r";\s*printing object\s+(.+?)\s*$", re.I | re.M)
_MESH_RE = re.compile(r";\s*MESH\s*:\s*(.+?)\s*$", re.I | re.M)

#: Cles portant la duree estimee, selon le trancheur.
_TIME_KEYS = (
    "estimated printing time (normal mode)",
    "estimated printing time",
    "time",
    "model printing time",
    "total estimated time",
)


def parse_duration(text: str) -> int | None:
    """'1h 20m 3s' / '2d 4h' / '4231' -> secondes."""
    text = text.strip()
    if not text:
        return None
    if text.replace(".", "", 1).isdigit():
        return int(float(text))
    match = _DURATION_RE.match(text)
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    total = days * 86400 + hours * 3600 + minutes * 60 + seconds
    return total or None


def count_objects(text: str) -> int | None:
    """Nombre de pieces posees sur le plateau, ou None si indeterminable.

    Les trancheurs ne l'annoncent pas directement: on le deduit des marqueurs
    d'objets. `EXCLUDE_OBJECT_DEFINE` est le plus fiable car emis en tete, une
    ligne par piece; les autres marqueurs sont semes dans le corps du fichier,
    donc leur comptage peut sous-estimer si le fichier n'est lu que par morceaux.
    """
    defined = _EXCLUDE_OBJECT_RE.findall(text)
    if defined:
        return len(set(defined))

    indexes = [int(value) for value in _M486_RE.findall(text)]
    positives = [index for index in indexes if index >= 0]
    if positives:
        return max(positives) + 1

    for pattern in (_PRINTING_OBJECT_RE, _MESH_RE):
        names = {name.strip() for name in pattern.findall(text)}
        names.discard("NONMESH")
        if names:
            return len(names)
    return None


def _split_list(value: str) -> list[str]:
    """Les trancheurs multi-materiaux separent par ';' ou ','."""
    parts = [p.strip().strip('"') for p in re.split(r"[;,]", value)]
    return [p for p in parts if p]


def _normalize_color(value: str) -> str | None:
    text = value.strip().lstrip("#")
    if len(text) == 8:
        text = text[:6]
    if len(text) == 6:
        try:
            int(text, 16)
        except ValueError:
            return None
        return f"#{text.upper()}"
    return None


@dataclass
class GcodeMetadata:
    slicer: str | None = None
    print_time_s: int | None = None
    layer_height: float | None = None
    first_layer_height: float | None = None
    layer_count: int | None = None
    object_height: float | None = None
    nozzle_diameter: float | None = None
    filament_types: list[str] = field(default_factory=list)
    filament_colors: list[str] = field(default_factory=list)
    filament_used_g: float | None = None
    filament_used_mm: float | None = None
    nozzle_temp: float | None = None
    bed_temp: float | None = None
    printer_model: str | None = None
    object_count: int | None = None
    printable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, [], {})}


def _iter_comment_lines(text: str) -> Iterable[tuple[str, str]]:
    """Rend les paires (cle, valeur) des lignes de commentaire.

    Accepte `; cle = valeur` (PrusaSlicer/Orca/Creality) et `;CLE:valeur` (Cura).
    """
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith(";"):
            continue
        body = line[1:].strip()
        # `; generated by OrcaSlicer 2.1 on 2026-05-02 at 12:34:56` n'a pas de
        # separateur exploitable: la cle est le prefixe lui-meme.
        if body.lower().startswith("generated by"):
            yield "generated by", body[len("generated by"):].strip()
        elif "=" in body:
            key, _, value = body.partition("=")
            yield key.strip().lower(), value.strip()
        elif ":" in body:
            key, _, value = body.partition(":")
            key = key.strip().lower()
            # Evite de confondre une phrase libre contenant ':' avec une cle.
            if key and len(key) <= 48:
                yield key, value.strip()


def _as_float(value: str) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(match.group()) if match else None


def _read_ends(path: Path) -> tuple[str, str]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        head = handle.read(min(HEAD_BYTES, size))
        if size > HEAD_BYTES:
            handle.seek(max(0, size - TAIL_BYTES))
            tail = handle.read(TAIL_BYTES)
        else:
            tail = b""
    return head.decode("utf-8", "replace"), tail.decode("utf-8", "replace")


def parse_gcode_text(text: str) -> GcodeMetadata:
    meta = GcodeMetadata()
    filament_g_parts: list[float] = []

    for key, value in _iter_comment_lines(text):
        if not value:
            continue
        if key in ("generated by", "generator"):
            meta.slicer = meta.slicer or value.split(",")[0].strip()
        elif key in _TIME_KEYS:
            meta.print_time_s = meta.print_time_s or parse_duration(value)
        elif key in ("layer_height", "layer height"):
            meta.layer_height = meta.layer_height or _as_float(value)
        elif key in ("first_layer_height", "initial layer height"):
            meta.first_layer_height = meta.first_layer_height or _as_float(value)
        elif key in ("total layer number", "layer_count", "layercount"):
            number = _as_float(value)
            meta.layer_count = meta.layer_count or (int(number) if number else None)
        elif key in ("max_z_height", "maxz", "object_height", "total height"):
            meta.object_height = meta.object_height or _as_float(value)
        elif key in ("nozzle_diameter", "machine_nozzle_size"):
            meta.nozzle_diameter = meta.nozzle_diameter or _as_float(value.split(",")[0])
        elif key in ("filament_type", "filament type", "material"):
            meta.filament_types = meta.filament_types or [
                t.upper() for t in _split_list(value) if t
            ]
        elif key in ("filament_colour", "filament_color", "extruder_colour", "filament colour"):
            colors = [c for c in (_normalize_color(v) for v in _split_list(value)) if c]
            meta.filament_colors = meta.filament_colors or colors
        elif key in ("filament used [g]", "total filament used [g]", "filament_used_g"):
            filament_g_parts = [f for f in (_as_float(v) for v in _split_list(value)) if f]
        elif key in ("filament used [mm]", "filament_used_mm"):
            values = [f for f in (_as_float(v) for v in _split_list(value)) if f]
            meta.filament_used_mm = meta.filament_used_mm or (sum(values) if values else None)
        elif key == "filament used":  # Cura: "1.23m"
            number = _as_float(value)
            if number and meta.filament_used_mm is None:
                meta.filament_used_mm = number * 1000 if "m" in value.lower() else number
        elif key in ("first_layer_temperature", "nozzle_temperature", "material_print_temperature"):
            meta.nozzle_temp = meta.nozzle_temp or _as_float(value.split(",")[0])
        elif key in ("first_layer_bed_temperature", "bed_temperature", "material_bed_temperature"):
            meta.bed_temp = meta.bed_temp or _as_float(value.split(",")[0])
        elif key in ("printer_model", "machine_name", "printer_settings_id", "target_machine.name"):
            meta.printer_model = meta.printer_model or value

    meta.object_count = count_objects(text)
    if filament_g_parts:
        meta.filament_used_g = round(sum(filament_g_parts), 2)
    if meta.slicer is None and "CrealityPrint" in text[:4000]:
        meta.slicer = "Creality Print"
    return meta


def extract_thumbnail(text: str) -> bytes | None:
    """Retourne la plus grande miniature encodee dans le G-code."""
    best: tuple[int, bytes] | None = None

    lines = text.splitlines()
    index = 0
    while index < len(lines):
        match = _THUMB_BEGIN_RE.search(lines[index])
        if not match:
            index += 1
            continue
        width, height = int(match.group(1)), int(match.group(2))
        chunks: list[str] = []
        index += 1
        while index < len(lines) and not _THUMB_END_RE.search(lines[index]):
            chunks.append(lines[index].lstrip(";").strip())
            index += 1
        index += 1
        try:
            payload = base64.b64decode("".join(chunks), validate=False)
        except (binascii.Error, ValueError):
            continue
        area = width * height
        if payload and (best is None or area > best[0]):
            best = (area, payload)

    if best is None:
        # Format Creality Print: `;gimage:` (grande) ou `;simage:` (petite).
        for match in _CREALITY_THUMB_RE.finditer(text):
            try:
                payload = base64.b64decode(match.group(2), validate=False)
            except (binascii.Error, ValueError):
                continue
            weight = 2 if match.group(1).lower() == "gimage" else 1
            if payload and (best is None or weight > best[0]):
                best = (weight, payload)

    return best[1] if best else None


def guess_image_extension(payload: bytes) -> str:
    for magic, extension in _MAGIC.items():
        if payload.startswith(magic):
            return extension
    return ".png"


def extract_gcode_metadata(path: Path) -> tuple[GcodeMetadata, bytes | None]:
    head, tail = _read_ends(Path(path))
    meta = parse_gcode_text(head)
    if tail:
        tail_meta = parse_gcode_text(tail)
        for key, value in tail_meta.__dict__.items():
            if not getattr(meta, key) and value:
                setattr(meta, key, value)
    thumbnail = extract_thumbnail(head) or (extract_thumbnail(tail) if tail else None)
    return meta, thumbnail
