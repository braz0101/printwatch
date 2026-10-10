"""Types partagés entre les modules."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum


class Kind(str, Enum):
    """CONSUMABLE : se vide (encre, toner). RECEPTACLE : se remplit (boîtier de maintenance, toner usagé)."""

    CONSUMABLE = "consumable"
    RECEPTACLE = "receptacle"


class Status(str, Enum):
    """Qualité de la valeur remontée par l'imprimante."""

    OK = "ok"                          # pourcentage exploitable
    SOME_REMAINING = "some_remaining"  # -3 : « il en reste », quantité non précisée
    UNKNOWN = "unknown"                # -2
    OTHER = "other"                    # -1
    NO_CAPACITY = "no_capacity"        # capacité absente ou non positive : pourcentage incalculable
    INVALID = "invalid"                # valeur illisible ou incohérente


class Level(IntEnum):
    OK = 0
    WARN = 1
    CRIT = 2


class EventType(str, Enum):
    WARN = "warn"
    CRITICAL = "critical"
    RESOLVED = "resolved"
    REPLACED = "replaced"
    DOWN = "down"
    UP = "up"


@dataclass(frozen=True)
class RawSupply:
    """Une ligne de prtMarkerSuppliesTable, telle que lue (colonnes OID .4 à .9)."""

    index: str                    # suffixe d'index, ex. "1.3"
    klass: int | None             # .4 prtMarkerSuppliesClass : 3 = se consomme, 4 = se remplit
    stype: int | None             # .5 prtMarkerSuppliesType : 4 = toner usagé, 8 = encre usagée, ...
    description: str              # .6
    unit: int | None              # .7 prtMarkerSuppliesSupplyUnit : 19 = pourcent
    max_capacity: int | None      # .8
    level: int | None             # .9


@dataclass(frozen=True)
class Supply:
    """Consommable normalisé. pct_remaining vaut None quand l'imprimante ne donne pas de valeur exploitable."""

    key: str
    description: str
    kind: Kind
    color: str | None
    status: Status
    pct_remaining: float | None
    level: int | None
    max_capacity: int | None
    unit: int | None


@dataclass(frozen=True)
class Printer:
    id: str
    name: str
    host: str
    brand: str = ""
    model: str = ""
    location: str = ""
    user: str = ""
    enabled: bool = True
