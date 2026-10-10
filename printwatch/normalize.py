"""Normalisation des lignes de la Printer MIB (RFC 3805) en niveaux comparables entre marques.

Règles :
* un consommable de classe 3 se vide : pourcentage restant = niveau / capacité ;
* un réceptacle (classe 4, ou types « toner usagé » / « encre usagée ») se remplit : on inverse,
  pour que « pourcentage restant » signifie partout « marge avant blocage » ;
* niveau -1 / -2 / -3 : autre / inconnu / il en reste (quantité non précisée), aucun pourcentage.
"""
from __future__ import annotations

import re

from .model import Kind, RawSupply, Status, Supply

CLASS_RECEPTACLE = 4
TYPES_RECEPTACLE = {4, 8}      # wasteToner, wasteInk
UNIT_PERCENT = 19

_COLORS: list[tuple[str, re.Pattern[str]]] = [
    ("waste", re.compile(r"\b(waste|maintenance|usag[ée]e?|r[ée]cup[ée]rateur|bo[iî]tier)\b", re.I)),
    ("black", re.compile(r"\b(black|noir|bk)\b", re.I)),
    ("cyan", re.compile(r"\bcyan\b", re.I)),
    ("magenta", re.compile(r"\bmagenta\b", re.I)),
    ("yellow", re.compile(r"\b(yellow|jaune)\b", re.I)),
]


def guess_color(description: str) -> str | None:
    """Couleur déduite du libellé (affichage uniquement) ; None si rien de reconnaissable."""
    for color, pattern in _COLORS:
        if pattern.search(description):
            return color
    return None


def normalize(raw: RawSupply) -> Supply:
    is_receptacle = raw.klass == CLASS_RECEPTACLE or raw.stype in TYPES_RECEPTACLE
    kind = Kind.RECEPTACLE if is_receptacle else Kind.CONSUMABLE
    color = "waste" if is_receptacle else guess_color(raw.description)

    def build(status: Status, pct: float | None = None) -> Supply:
        return Supply(
            key=raw.index,
            description=raw.description,
            kind=kind,
            color=color,
            status=status,
            pct_remaining=pct,
            level=raw.level,
            max_capacity=raw.max_capacity,
            unit=raw.unit,
        )

    level = raw.level
    if level is None:
        return build(Status.INVALID)
    if level == -1:
        return build(Status.OTHER)
    if level == -2:
        return build(Status.UNKNOWN)
    if level == -3:
        return build(Status.SOME_REMAINING)
    if level < 0:
        return build(Status.INVALID)

    capacity = raw.max_capacity
    if capacity is None or capacity <= 0:
        # Certaines imprimantes ne donnent pas de capacité mais expriment déjà le niveau en pourcentage.
        if raw.unit == UNIT_PERCENT and level <= 100:
            capacity = 100
        else:
            return build(Status.NO_CAPACITY)

    ratio = min(max(level / capacity, 0.0), 1.0) * 100.0
    remaining = 100.0 - ratio if is_receptacle else ratio
    return build(Status.OK, round(remaining, 1))
