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


COLORS = ("black", "cyan", "magenta", "yellow")
SUPPLY_GROUPS = (*COLORS, "box", "other")       # ce que l'on peut suivre : les quatre encres, le boîtier de maintenance, le reste


def supply_group(kind: str, color: str | None) -> str:
    """Groupe de suivi d'un consommable : sa couleur d'encre, `box` (boîtier de maintenance, toner usagé) ou `other`."""
    if kind == "receptacle":
        return "box"
    return color if color in COLORS else "other"


@dataclass(frozen=True)
class AlertSettings:
    """Réglages modifiables depuis le dashboard : seuils (en % restant) et consommables qui déclenchent des alertes.
    Un consommable non suivi reste affiché mais ne passe jamais en attention ni en critique."""
    warn: float = 15.0
    crit: float = 5.0
    tracked: frozenset[str] = frozenset(SUPPLY_GROUPS)

    def __post_init__(self) -> None:
        if not (0 <= self.crit < self.warn <= 100):
            raise ValueError("seuils invalides : il faut 0 ≤ critique < attention ≤ 100")
        unknown = set(self.tracked) - set(SUPPLY_GROUPS)
        if unknown:
            raise ValueError(f"consommable inconnu : {', '.join(sorted(unknown))}")
        if not self.tracked:
            raise ValueError("suivre au moins un consommable")

    def tracks(self, kind: str, color: str | None) -> bool:
        return supply_group(kind, color) in self.tracked

    def thresholds(self, hysteresis: float = 5.0) -> Thresholds:
        return Thresholds(self.warn, self.crit, hysteresis)

    def as_dict(self) -> dict:
        return {"warn": self.warn, "crit": self.crit, "inks": [g for g in SUPPLY_GROUPS if g in self.tracked]}

    @classmethod
    def from_dict(cls, d: dict, default: "AlertSettings") -> "AlertSettings":
        return cls(float(d.get("warn", default.warn)), float(d.get("crit", default.crit)),
                   frozenset(d.get("inks", default.tracked)))


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
