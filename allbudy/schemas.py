"""Schemas d'entree/sortie de l'API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import JobStatus, StorageKind, TransportKind

HexColor = str


def _validate_hex(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if not text.startswith("#"):
        text = f"#{text}"
    if len(text) != 7:
        raise ValueError("Couleur attendue au format #RRGGBB")
    int(text[1:], 16)  # leve ValueError si invalide
    return text.upper()


# --------------------------------------------------------------------- auth
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    is_admin: bool
    last_login: datetime | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6, max_length=128)


# ----------------------------------------------------------------- printers
class PrinterBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    model: str = "K1"
    transport: TransportKind = TransportKind.MOONRAKER
    host: str = "127.0.0.1"
    port: int | None = None
    api_key: str | None = None
    serial: str | None = None
    enabled: bool = True
    auto_assign: bool = True
    nozzle_diameter: float = Field(default=0.4, gt=0, le=2.0)
    tags: list[str] = Field(default_factory=list)
    has_cfs: bool = False
    camera_url: str | None = None
    upload_root: str = "gcodes"
    notes: str | None = None
    sort_order: int = 0


class PrinterCreate(PrinterBase):
    pass


class PrinterUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    model: str | None = None
    transport: TransportKind | None = None
    host: str | None = None
    port: int | None = None
    api_key: str | None = None
    serial: str | None = None
    enabled: bool | None = None
    auto_assign: bool | None = None
    nozzle_diameter: float | None = Field(default=None, gt=0, le=2.0)
    tags: list[str] | None = None
    has_cfs: bool | None = None
    camera_url: str | None = None
    upload_root: str | None = None
    notes: str | None = None
    sort_order: int | None = None


class PrinterOut(PrinterBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime | None = None


# ------------------------------------------------------------------- spools
class SpoolBase(BaseModel):
    printer_id: int | None = None
    unit: int = 0
    slot: int = Field(default=0, ge=0, le=15)
    material: str = "PLA"
    color_hex: HexColor = "#7F8C8D"
    color_name: str | None = None
    vendor: str | None = None
    remaining_g: float | None = Field(default=None, ge=0)
    total_g: float | None = Field(default=1000.0, ge=0)
    active: bool = False
    empty: bool = False

    @field_validator("color_hex")
    @classmethod
    def _color(cls, value: str) -> str:
        return _validate_hex(value) or "#7F8C8D"


class SpoolCreate(SpoolBase):
    pass


class SpoolUpdate(BaseModel):
    printer_id: int | None = None
    unit: int | None = None
    slot: int | None = Field(default=None, ge=0, le=15)
    material: str | None = None
    color_hex: HexColor | None = None
    color_name: str | None = None
    vendor: str | None = None
    remaining_g: float | None = Field(default=None, ge=0)
    total_g: float | None = Field(default=None, ge=0)
    active: bool | None = None
    empty: bool | None = None

    @field_validator("color_hex")
    @classmethod
    def _color(cls, value: str | None) -> str | None:
        return _validate_hex(value)


class SpoolOut(SpoolBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    managed: bool = False
    updated_at: datetime | None = None


# --------------------------------------------------------------------- jobs
class JobCreate(BaseModel):
    file_id: int
    name: str | None = None
    priority: int = 0
    copies: int = Field(default=1, ge=1, le=999)
    required_material: str | None = None
    required_color: HexColor | None = None
    color_tolerance: int = Field(default=40, ge=0, le=255)
    required_nozzle: float | None = Field(default=None, gt=0, le=2.0)
    required_tags: list[str] = Field(default_factory=list)
    allowed_printers: list[int] = Field(default_factory=list)
    auto_start: bool = True
    printer_id: int | None = None
    """Force l'envoi sur une machine precise (court-circuite le matching)."""

    @field_validator("required_color")
    @classmethod
    def _color(cls, value: str | None) -> str | None:
        return _validate_hex(value)


class JobUpdate(BaseModel):
    name: str | None = None
    priority: int | None = None
    position: int | None = None
    copies: int | None = Field(default=None, ge=1, le=999)
    required_material: str | None = None
    required_color: HexColor | None = None
    color_tolerance: int | None = Field(default=None, ge=0, le=255)
    required_nozzle: float | None = Field(default=None, gt=0, le=2.0)
    required_tags: list[str] | None = None
    allowed_printers: list[int] | None = None
    auto_start: bool | None = None
    status: JobStatus | None = None

    @field_validator("required_color")
    @classmethod
    def _color(cls, value: str | None) -> str | None:
        return _validate_hex(value)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    file_id: int
    printer_id: int | None
    status: str
    priority: int
    position: int
    copies: int
    copies_done: int
    progress: float
    auto_start: bool
    required_material: str | None
    required_color: str | None
    color_tolerance: int
    required_nozzle: float | None
    required_tags: list[Any]
    allowed_printers: list[Any]
    remote_filename: str | None
    error: str | None
    attempts: int
    created_at: datetime | None
    assigned_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None


class QueueReorder(BaseModel):
    job_ids: list[int]
    """Nouvel ordre complet de la file, du premier au dernier."""


# ------------------------------------------------------------------ storage
class StorageBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: StorageKind = StorageKind.SFTP
    host: str
    port: int | None = None
    username: str = ""
    remote_path: str = "/"
    enabled: bool = True
    auto_import: bool = False
    sync_interval: int = Field(default=900, ge=60, le=86400)


class StorageCreate(StorageBase):
    password: str | None = None


class StorageUpdate(BaseModel):
    name: str | None = None
    kind: StorageKind | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None
    remote_path: str | None = None
    enabled: bool | None = None
    auto_import: bool | None = None
    sync_interval: int | None = Field(default=None, ge=60, le=86400)


class StorageOut(StorageBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    last_sync: datetime | None = None
    last_error: str | None = None
    has_password: bool = False


# ----------------------------------------------------------------- commandes
class TemperatureCommand(BaseModel):
    heater: Literal["extruder", "bed", "chamber"]
    value: float = Field(ge=0, le=350)


class FanCommand(BaseModel):
    fan: Literal["part", "aux", "chamber"] = "part"
    speed: float = Field(ge=0, le=100)


class MoveCommand(BaseModel):
    axis: Literal["X", "Y", "Z", "x", "y", "z"]
    distance: float = Field(ge=-300, le=300)
    speed: float = Field(default=3000, gt=0, le=30000)


class ExtrudeCommand(BaseModel):
    distance: float = Field(ge=-200, le=200)
    speed: float = Field(default=300, gt=0, le=6000)


class GcodeCommand(BaseModel):
    script: str = Field(min_length=1, max_length=8000)


class LightCommand(BaseModel):
    on: bool


class HomeCommand(BaseModel):
    axes: str = "XYZ"


class SpeedCommand(BaseModel):
    percent: float = Field(ge=10, le=300)


class PrintCommand(BaseModel):
    file_id: int
    start: bool = True


class DiscoveryRequest(BaseModel):
    network: str | None = None
    ports: list[int] | None = None


class MessageResponse(BaseModel):
    ok: bool = True
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
