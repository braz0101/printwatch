"""Mode démonstration : parc fictif et relevés SNMP simulés, pour essayer le dashboard sans imprimante (ni net-snmp).

    printwatch serve --demo        # base jetable `demo.db`, un cycle par minute, bouton « Synchroniser » opérationnel

Les niveaux baissent au fil des minutes (pour voir passer les alertes). Tout hôte ajouté depuis le dashboard obtient un parc synthétique
déterministe, sauf `10.9.x.x` et les adresses en `.255`, qui « ne répondent pas ». Aucune donnée réelle : noms et utilisateurs fictifs.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Callable

from .ledm import LedmSupply
from .model import Printer, RawSupply
from .snmp import SnmpError


@dataclass(frozen=True)
class Spec:
    key: str
    description: str
    base: float           # % restant au démarrage ; -2 = « inconnu » (cartouche HP très basse)
    rate: float = 0.1     # % consommés par minute de démonstration


def _epson(k: float, c: float, m: float, y: float) -> list[Spec]:
    return [Spec("1.1", "Black Ink Supply Unit T05B1/T05A1", k, 0.06), Spec("1.2", "Cyan Ink Supply Unit T05B2/T05A2", c, 0.09),
            Spec("1.3", "Magenta Ink Supply Unit T05B3/T05A3", m, 0.12), Spec("1.4", "Yellow Ink Supply Unit T05B4/T05A4", y, 0.05)]


def _hp(k: float, c: float, m: float, y: float) -> list[Spec]:
    return [Spec("1.1", "Black Cartridge HP W2030A", k, 0.05), Spec("1.2", "Cyan Cartridge HP W2031A", c, 0.05),
            Spec("1.3", "Magenta Cartridge HP W2033A", m, 0.06), Spec("1.4", "Yellow Cartridge HP W2032A", y, 0.05)]


# (fiche, relevés ; None = injoignable)
FLEET: list[tuple[Printer, list[Spec] | None]] = [
    (Printer("demo-11", "Pool Comptabilité", "192.0.2.11", "EPSON", "WF-C879RDWF", "BAT A", "Marie DUPONT"), _epson(30, 83, 18, 23)),
    (Printer("demo-12", "Pool RH", "192.0.2.12", "EPSON", "WF-C5890DWF", "BAT A", "Paul MARTIN"), _epson(40, 78, 1, 1)),
    (Printer("demo-13", "Pool Achats", "192.0.2.13", "EPSON", "WF-C20750D4TW", "Approvisionnement", "Julie BERNARD"),
     [Spec("1.1", "Black Ink Cartridge T02S1", 88, 0.04), Spec("1.2", "Black Ink Cartridge T02S1", 100, 0.02),
      Spec("1.3", "Cyan Ink Cartridge T02S2", 1, 0.0), Spec("1.4", "Magenta Ink Cartridge T02S3", 57, 0.08),
      Spec("1.5", "Yellow Ink Cartridge T02S4", 6, 0.05)]),
    (Printer("demo-14", "Pool Trésorerie", "192.0.2.14", "EPSON", "WF-C879RDWF", "BAT A", "Luc MOREAU"), _epson(25, 50, 79, 82)),
    (Printer("demo-15", "Pool ADV", "192.0.2.15", "EPSON", "WF-C879RDWF", "BAT B niveau 1 / ADV", "Sophie PETIT"), _epson(76, 37, 38, 83)),
    (Printer("demo-16", "Labo", "192.0.2.16", "EPSON", "WF-C5890DWF", "laboratoire", "Antoine ROUX"), _epson(94, 98, 51, 99)),
    (Printer("demo-17", "Pool BAT C", "192.0.2.17", "EPSON", "WF-C879RDWF", "BAT C", "Claire FONTAINE"), _epson(1, 79, 75, 98)),
    (Printer("demo-18", "HP M479 (niveaux partiels)", "192.0.2.18", "HP", "Color LaserJet Pro MFP M479dw", "Accueil", "Hugo LAMBERT"), _hp(-2, -2, 7, -2)),
    (Printer("demo-19", "HP M181fw", "192.0.2.19", "HP", "Color LaserJet MFP M181fw", "", "Emma GIRARD"), _hp(32, 50, 51, 65)),
    (Printer("demo-20", "Pool Salle technique", "192.0.2.20", "EPSON", "WF-C5890DWF", "Salle technique", "Poste opérateurs"), None),
    (Printer("demo-21", "Pool Maintenance", "192.0.2.21", "EPSON", "WF-C529RDTW", "maintenance", ""), None),
]
_BY_HOST = {p.host: (p, specs) for p, specs in FLEET}
# Cartouches « très basses » que l'interface web d'une HP publie alors que SNMP répond -2 (voir ledm.py).
_HP_VERY_LOW = {"192.0.2.18": [LedmSupply("black", "W2030A", 0, "veryLow", "HP"), LedmSupply("cyan", "W2031A", 0, "veryLow", "HP"),
                               LedmSupply("yellow", "W2032A", 0, "veryLow", "HP")]}


class DemoClient:
    """Implémente le protocole SnmpClient sans réseau : niveaux déterministes qui décroissent avec le temps."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock, self.t0 = clock, clock()

    @staticmethod
    def _synthetic(host: str) -> list[Spec] | None:
        if host.startswith("10.9.") or host.endswith(".255"):
            return None
        h = hashlib.md5(host.encode(), usedforsecurity=False).digest()
        return _epson(*(20 + h[i] % 76 for i in range(4)))

    def _specs(self, host: str) -> list[Spec]:
        specs = _BY_HOST[host][1] if host in _BY_HOST else self._synthetic(host)
        if specs is None:
            raise SnmpError(f"Timeout: No Response from {host} (démo)")
        return specs

    def supplies(self, host: str) -> list[RawSupply]:
        minutes = (self.clock() - self.t0) / 60
        return [RawSupply(s.key, 3, 3 if "Cartridge HP" in s.description else 5, s.description, 19, 100,
                          -2 if s.base < 0 else int(max(0.0, s.base - s.rate * minutes))) for s in self._specs(host)]

    def sys_descr(self, host: str) -> str | None:
        self._specs(host)
        return "HP ETHERNET MULTI-ENVIRONMENT (démo)" if host in _BY_HOST and _BY_HOST[host][0].brand == "HP" else "EPSON Built-in Print Server (démo)"

    def ledm(self, host: str) -> list[LedmSupply]:
        return list(_HP_VERY_LOW.get(host, []))
