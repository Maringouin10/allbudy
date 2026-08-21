"""File d'attente: attribution des travaux aux imprimantes."""

from ..printers.manager import manager
from .matcher import MatchResult, Requirement, color_distance, evaluate, normalize_material
from .scheduler import JobScheduler

#: Dispatcher unique de l'instance.
scheduler = JobScheduler(manager)

__all__ = [
    "JobScheduler",
    "MatchResult",
    "Requirement",
    "color_distance",
    "evaluate",
    "normalize_material",
    "scheduler",
]
