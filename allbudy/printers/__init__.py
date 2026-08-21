"""Transports et gestion du parc d'imprimantes."""

from .base import PrinterError, PrinterStatus, PrinterTransport, SpoolState, UnsupportedOperation
from .manager import PrinterManager, manager

__all__ = [
    "PrinterError",
    "PrinterStatus",
    "PrinterTransport",
    "SpoolState",
    "UnsupportedOperation",
    "PrinterManager",
    "manager",
]
