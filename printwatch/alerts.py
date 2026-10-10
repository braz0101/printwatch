"""Machine à états des alertes : un état par consommable, avec hystérésis.

Tous les seuils s'appliquent au pourcentage *restant* (pour un réceptacle, 5 % restant = 95 % plein).
L'hystérésis évite qu'une jauge qui oscille autour d'un seuil produise une rafale de courriels.
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import EventType, Level


@dataclass(frozen=True)
class Thresholds:
    warn: float = 15.0
    crit: float = 5.0
    hysteresis: float = 5.0

    def __post_init__(self) -> None:
        if not (0 <= self.crit < self.warn <= 100):
            raise ValueError("seuils invalides : il faut 0 <= critique < avertissement <= 100")
        if self.hysteresis < 0:
            raise ValueError("l'hystérésis ne peut pas être négative")

    def raw_level(self, pct: float) -> Level:
        if pct <= self.crit:
            return Level.CRIT
        if pct <= self.warn:
            return Level.WARN
        return Level.OK


def next_level(prev: Level, pct: float, th: Thresholds, replaced: bool = False) -> Level:
    """Nouvel état. Une dégradation est immédiate ; un retour à la normale exige de dépasser le seuil + hystérésis.

    `replaced` (remplacement détecté) remet l'état à zéro sans hystérésis.
    """
    raw = th.raw_level(pct)
    if replaced or raw >= prev:
        return raw
    cleared_crit = pct > th.crit + th.hysteresis
    cleared_warn = pct > th.warn + th.hysteresis
    if prev == Level.CRIT:
        if not cleared_crit:
            return Level.CRIT
        return Level.OK if cleared_warn else Level.WARN
    # prev == WARN et raw == OK
    return Level.OK if cleared_warn else Level.WARN


def transition(prev: Level, new: Level) -> EventType | None:
    """Événement à émettre pour un changement d'état. CRIT -> WARN est une amélioration silencieuse."""
    if new == prev:
        return None
    if new == Level.CRIT:
        return EventType.CRITICAL
    if new == Level.WARN:
        return EventType.WARN if prev == Level.OK else None
    return EventType.RESOLVED
