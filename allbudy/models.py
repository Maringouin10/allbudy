"""Modeles SQLAlchemy (SQLite) d'AllBudy."""
from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class TransportKind(str, enum.Enum):
    """Protocole utilise pour piloter l'imprimante."""

    MOONRAKER = "moonraker"
    """Klipper/Moonraker (K1, K1C, K1 Max, K2 Plus, Ender-3 V3 avec Creality OS, Sonic Pad)."""
    CREALITY_LAN = "creality_lan"
    """WebSocket proprietaire Creality port 9999 (firmware stock, mode LAN/developpeur)."""
    SIMULATOR = "simulator"
    """Imprimante simulee, pour la demo et les tests."""


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    ASSIGNED = "assigned"
    SENDING = "sending"
    PRINTING = "printing"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_JOB_STATUSES = (
    JobStatus.ASSIGNED,
    JobStatus.SENDING,
    JobStatus.PRINTING,
    JobStatus.PAUSED,
)


class StorageKind(str, enum.Enum):
    FTP = "ftp"
    FTPS = "ftps"
    SFTP = "sftp"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Printer(Base):
    __tablename__ = "printers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    model: Mapped[str] = mapped_column(String(64), default="K1")
    transport: Mapped[str] = mapped_column(String(32), default=TransportKind.MOONRAKER.value)
    host: Mapped[str] = mapped_column(String(255), default="127.0.0.1")
    port: Mapped[int] = mapped_column(Integer, default=7125)
    api_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    serial: Mapped[str | None] = mapped_column(String(120), nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_assign: Mapped[bool] = mapped_column(Boolean, default=True)
    """L'imprimante peut recevoir automatiquement des travaux de la file."""

    nozzle_diameter: Mapped[float] = mapped_column(Float, default=0.4)
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    """Etiquettes libres (ex: ["abs", "chambre-chauffee"]) filtrables par la file."""
    has_cfs: Mapped[bool] = mapped_column(Boolean, default=False)
    """L'imprimante dispose d'un CFS (Creality Filament System)."""
    camera_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    upload_root: Mapped[str] = mapped_column(String(64), default="gcodes")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    spools: Mapped[list[Spool]] = relationship(
        back_populates="printer", cascade="all, delete-orphan"
    )
    jobs: Mapped[list[Job]] = relationship(back_populates="printer")


class GcodeFile(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), index=True)
    stored_name: Mapped[str] = mapped_column(String(255), unique=True)
    """Nom sur disque, unique, sous data/files."""
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True, default="")
    kind: Mapped[str] = mapped_column(String(16), default="gcode")  # gcode | 3mf
    source: Mapped[str] = mapped_column(String(32), default="upload")  # upload | remote
    remote_id: Mapped[int | None] = mapped_column(
        ForeignKey("remote_storages.id", ondelete="SET NULL"), nullable=True
    )
    remote_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    thumbnail: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    """Metadonnees extraites du tranchage: temps, filament, couches, materiaux, couleurs..."""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    jobs: Mapped[list[Job]] = relationship(back_populates="file")


class Spool(Base):
    """Bobine de filament: emplacement CFS, ou bobine externe sur support."""

    __tablename__ = "spools"
    __table_args__ = (UniqueConstraint("printer_id", "unit", "slot", name="uq_spool_slot"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    printer_id: Mapped[int | None] = mapped_column(
        ForeignKey("printers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    """NULL = bobine en stock, non montee sur une machine."""
    unit: Mapped[int] = mapped_column(Integer, default=0)
    """Index du boitier CFS (0 = premier CFS, -1 = support externe)."""
    slot: Mapped[int] = mapped_column(Integer, default=0)
    """Emplacement dans le CFS (0..3)."""

    material: Mapped[str] = mapped_column(String(32), default="PLA")
    color_hex: Mapped[str] = mapped_column(String(9), default="#7f8c8d")
    color_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vendor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    remaining_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_g: Mapped[float | None] = mapped_column(Float, default=1000.0)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    """Filament actuellement charge jusqu'a la buse."""
    empty: Mapped[bool] = mapped_column(Boolean, default=False)
    managed: Mapped[bool] = mapped_column(Boolean, default=False)
    """True = synchronise depuis le CFS, ne pas ecraser manuellement."""
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    printer: Mapped[Printer | None] = relationship(back_populates="spools")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    printer_id: Mapped[int | None] = mapped_column(
        ForeignKey("printers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.QUEUED.value, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    """Plus grand = plus prioritaire."""
    position: Mapped[int] = mapped_column(Integer, default=0)
    """Ordre manuel dans la file (croissant)."""

    # Contraintes d'attribution
    required_material: Mapped[str | None] = mapped_column(String(32), nullable=True)
    required_color: Mapped[str | None] = mapped_column(String(9), nullable=True)
    color_tolerance: Mapped[int] = mapped_column(Integer, default=40)
    """Distance RGB maximale toleree pour considerer deux couleurs identiques."""
    required_nozzle: Mapped[float | None] = mapped_column(Float, nullable=True)
    required_tags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    allowed_printers: Mapped[list[Any]] = mapped_column(JSON, default=list)
    """Liste d'ids d'imprimantes autorisees; vide = toutes."""

    copies: Mapped[int] = mapped_column(Integer, default=1)
    copies_done: Mapped[int] = mapped_column(Integer, default=0)
    auto_start: Mapped[bool] = mapped_column(Boolean, default=True)

    progress: Mapped[float] = mapped_column(Float, default=0.0)
    remote_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    file: Mapped[GcodeFile] = relationship(back_populates="jobs")
    printer: Mapped[Printer | None] = relationship(back_populates="jobs")


class RemoteStorage(Base):
    """Depot distant FTP/FTPS/SFTP synchronise avec la bibliotheque locale."""

    __tablename__ = "remote_storages"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    kind: Mapped[str] = mapped_column(String(16), default=StorageKind.SFTP.value)
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=22)
    username: Mapped[str] = mapped_column(String(120), default="")
    secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Mot de passe chiffre (Fernet) avec la cle secrete de l'instance."""
    remote_path: Mapped[str] = mapped_column(String(512), default="/")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_import: Mapped[bool] = mapped_column(Boolean, default=False)
    """Importe automatiquement les nouveaux fichiers du depot dans la bibliotheque."""
    sync_interval: Mapped[int] = mapped_column(Integer, default=900)
    last_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EventLog(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    category: Mapped[str] = mapped_column(String(32), default="system")
    message: Mapped[str] = mapped_column(Text)
    printer_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Setting(Base):
    """Reglages runtime modifiables depuis l'UI."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
