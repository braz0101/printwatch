#!/usr/bin/env python3
"""Génère une base SQLite de DÉMONSTRATION : imprimantes fictives, 30 jours de relevés, remplacement, panne.

Sert à voir les courbes et la rupture estimée du dashboard sans attendre des jours de collecte réelle.
Jamais sur la base de production : le script refuse d'écraser un fichier existant sans --force.

    python tools/seed_demo.py data/demo.db                    # 4 imprimantes
    python tools/seed_demo.py data/demo.db --profile audit    # 18 imprimantes (parc de l'audit), seuil d'attention à 30 %
    printwatch -c demo.toml serve        # demo.toml : [collector] db_path = "data/demo.db"
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from printwatch.forecast import estimate  # noqa: E402
from printwatch.model import EventType, Kind, Printer, Status, Supply  # noqa: E402
from printwatch.storage import Storage  # noqa: E402

DAY = 86400.0
STEP = 6 * 3600.0            # un relevé toutes les 6 h
WARN, CRIT = 15.0, 5.0


@dataclass(frozen=True)
class Spec:
    key: str
    description: str
    color: str | None
    kind: Kind
    start: float | None       # % restant il y a 30 jours ; None = « cartouche présente » sans pourcentage (code -3)
    rate: float               # % consommés par jour
    replace_day: float | None = None   # jour (0 = il y a 30 j) où la cartouche est remplacée (retour à 100 %)


INK = [
    ("1.1", "Black Ink Cartridge", "black"), ("1.2", "Cyan Ink Cartridge", "cyan"),
    ("1.3", "Magenta Ink Cartridge", "magenta"), ("1.4", "Yellow Ink Cartridge", "yellow"),
]

PRINTERS: list[tuple[Printer, list[Spec], bool]] = [
    (Printer("demo-1", "DEMO-Compta-1", "192.0.2.11", "Epson", "WF-C879R", "Bureau A1"), [
        Spec("1.1", "Black Ink Cartridge", "black", Kind.CONSUMABLE, 88, 1.9),
        Spec("1.2", "Cyan Ink Cartridge", "cyan", Kind.CONSUMABLE, 60, 1.6),     # ~ 12 % aujourd'hui, avertissement
        Spec("1.3", "Magenta Ink Cartridge", "magenta", Kind.CONSUMABLE, 75, 1.1),
        Spec("1.4", "Yellow Ink Cartridge", "yellow", Kind.CONSUMABLE, 95, 0.8),
        Spec("1.5", "Maintenance Box", None, Kind.RECEPTACLE, 46, 1.3),           # ~ 7 % restant
    ], False),
    (Printer("demo-2", "DEMO-RH-1", "192.0.2.12", "Epson", "WF-C879R", "Bureau B3"), [
        Spec("1.1", "Black Ink Cartridge", "black", Kind.CONSUMABLE, 55, 2.4, replace_day=18),  # remplacement + chute
        Spec("1.2", "Cyan Ink Cartridge", "cyan", Kind.CONSUMABLE, 90, 0.7),
        Spec("1.3", "Magenta Ink Cartridge", "magenta", Kind.CONSUMABLE, 85, 0.9),
        Spec("1.4", "Yellow Ink Cartridge", "yellow", Kind.CONSUMABLE, 92, 0.6),
        Spec("1.5", "Maintenance Box", None, Kind.RECEPTACLE, 80, 0.5),
    ], False),
    (Printer("demo-3", "DEMO-Direction-1", "192.0.2.13", "Epson", "WF-C879R", "Bureau D2"), [
        Spec("1.1", "Black Ink Cartridge", "black", Kind.CONSUMABLE, 46, 1.45),     # ~ 2,5 % : critique
        Spec("1.2", "Cyan Ink Cartridge", "cyan", Kind.CONSUMABLE, 78, 1.0),
        Spec("1.3", "Magenta Ink Cartridge", "magenta", Kind.CONSUMABLE, 70, 0.9),
        Spec("1.4", "Yellow Ink Cartridge", "yellow", Kind.CONSUMABLE, 82, 0.7),
        Spec("1.5", "Maintenance Box", None, Kind.RECEPTACLE, 65, 0.6),
    ], False),
    (Printer("demo-4", "DEMO-Archives-1", "192.0.2.14", "Epson", "WF-C879R", "Sous-sol"), [
        Spec("1.1", "Black Ink Cartridge", "black", Kind.CONSUMABLE, 70, 0.4),
        Spec("1.2", "Cyan Ink Cartridge", "cyan", Kind.CONSUMABLE, 70, 0.3),
        Spec("1.3", "Magenta Ink Cartridge", "magenta", Kind.CONSUMABLE, 70, 0.3),
        Spec("1.4", "Yellow Ink Cartridge", "yellow", Kind.CONSUMABLE, 70, 0.3),
        Spec("1.5", "Maintenance Box", None, Kind.RECEPTACLE, 90, 0.2),
    ], True),                                                                    # injoignable
]


# Parc « audit » : 18 imprimantes calquées sur le tableau d'audit du dossier technique (niveau le plus bas par machine).
# Adresses fictives en 192.0.2.0/24 (RFC 5737) : le dernier octet reprend celui de l'audit.
def _cmyk(low_key: str, low: float, others: tuple[float, float, float], rate: float = 0.9) -> list[Spec]:
    keys = [("1.1", "Black Ink Cartridge", "black"), ("1.2", "Cyan Ink Cartridge", "cyan"),
            ("1.3", "Magenta Ink Cartridge", "magenta"), ("1.4", "Yellow Ink Cartridge", "yellow")]
    rest = iter(others)
    out = []
    for key, desc, color in keys:
        lvl = low if key == low_key else next(rest)
        out.append(Spec(key, desc, color, Kind.CONSUMABLE, min(100.0, lvl + rate * 30 * (0.6 + 0.4 * (len(out) % 3))), rate * (0.6 + 0.4 * (len(out) % 3))))
    return out


def _mono(level: float, rate: float = 0.9, replace_day: float | None = None) -> list[Spec]:
    return [Spec("1.1", "Black Toner Cartridge", "black", Kind.CONSUMABLE, min(100.0, level + rate * 30), rate, replace_day)]


def _present() -> list[Spec]:
    return [Spec("1.1", "Black Toner Cartridge", "black", Kind.CONSUMABLE, None, 0.0)]


# Inventaire fictif (noms et lieux, sans IP ni utilisateur) : dernier octet de l'audit -> (nom, marque, modèle, lieu).
INVENTORY: dict[int, tuple[str, str, str, str]] = {
    51: ("Pool Salle technique", "Epson", "WF-C5890DWF", "Salle technique"),
    73: ("Labo Équipe 1", "Epson", "WF-C5890DWF", "Laboratoire"),
    80: ("Pool BAT A étage", "Epson", "WF-C879RDWF", "BAT A"),
    78: ("Pool RH", "Epson", "WF-C5890DWF", "BAT A"),
    77: ("Pool Accueil", "Epson", "WF-C879RDWF", "Accueil"),
    76: ("Pool Inspection", "Epson", "WF-C5890DWF", "BAT C"),
    75: ("Pool Achats", "Epson", "WF-C20750D4TW", "Approvisionnement"),
    74: ("Pool Maintenance", "Epson", "WF-C529RDTW", "Maintenance"),
    72: ("Pool BAT C", "Epson", "WF-C879RDWF", "BAT C"),
    71: ("Pool Comptabilité", "Epson", "WF-C879RDWF", "BAT A"),
    69: ("Pool ADV", "Epson", "WF-C879RDWF", "BAT B niveau 1 / ADV"),
    68: ("Pool Préparation", "Epson", "WF-C879RDWF", "Maintenance"),
    67: ("Pool Contrôle de gestion", "Epson", "WF-C5890DWF", "BAT B RDC"),
    66: ("Pool Service 2", "Epson", "WF-C529RDTW", "BAT B RDC"),
    70: ("Pool Trésorerie", "Epson", "WF-C879RDWF", "BAT A"),
    82: ("Informatique", "Epson", "WF-C5890DWF", "BAT B niveau 1 / Informatique"),
    89: ("Pool Accueil (HP)", "HP", "Color LaserJet Pro MFP M479fdn", "Accueil"),
    105: ("HP à identifier", "HP", "", ""),
}


def audit_fleet() -> list[tuple[Printer, list[Spec], bool]]:
    rows: list[tuple[int, list[Spec], bool]] = [
        (72, _cmyk("1.1", 1, (45, 58, 63), 1.1), False),
        (70, _cmyk("1.2", 25, (62, 71, 80), 0.8), False),
        (67, _mono(26, 1.0), False),
        (71, _cmyk("1.3", 30, (64, 55, 77), 0.7), False),
        (69, _mono(76, 0.6), False),
        (82, _cmyk("1.1", 77, (83, 88, 91), 0.5), False),
        (73, _mono(96, 0.2), False),
        (80, _mono(98, 0.15), False),
        (75, _mono(100, 0.9, replace_day=27), False),
        (89, _present(), False),
        (105, _present(), False),
    ] + [(n, _mono(60, 0.3), True) for n in (78, 51, 77, 76, 74, 68, 66)]
    out = []
    for octet, specs, down in sorted(rows, key=lambda r: r[0]):
        name, brand, model, loc = INVENTORY[octet]
        out.append((Printer(f"demo-{octet}", f"DEMO {name}", f"192.0.2.{octet}", brand, model, loc), specs, down))
    return out


def level_of(pct: float) -> int:
    return 2 if pct <= CRIT else 1 if pct <= WARN else 0


def value_at(spec: Spec, day: float) -> float:
    assert spec.start is not None
    if spec.replace_day is not None and day >= spec.replace_day:
        return max(0.0, 100.0 - spec.rate * (day - spec.replace_day))
    return max(0.0, spec.start - spec.rate * day)


def seed(path: str, *, days: int = 30, now: float | None = None,
         fleet: list[tuple[Printer, list[Spec], bool]] | None = None) -> None:
    fleet = PRINTERS if fleet is None else fleet
    now = now or time.time()
    t0 = now - days * DAY
    st = Storage(path)
    events: list[tuple] = []       # insérés triés par date à la fin : l'identifiant suit alors la chronologie
    try:
        for printer, specs, down in fleet:
            st.upsert_printer(printer)
            steps = int(days * DAY // STEP) + 1
            last_ok = now - (3 * DAY if down else 0)       # l'imprimante injoignable n'a plus répondu depuis 3 jours
            for i in range(steps):
                ts = t0 + i * STEP
                if ts > last_ok:
                    break
                for sp in specs:
                    if sp.start is None:
                        continue
                    pct = round(value_at(sp, (ts - t0) / DAY), 1)
                    st.upsert_supply(printer.id, Supply(
                        sp.key, sp.description, sp.kind, sp.color, Status.OK, pct, int(pct), 100, 19),
                        level_of(pct), ts)
            for sp in specs:                                  # prévision de rupture, comme le fait le collecteur à chaque cycle
                if sp.start is not None:
                    est = estimate(st.history(printer.id, sp.key, last_ok - 35 * DAY))
                    st.set_forecast(printer.id, sp.key, est.days_left if est else None, est.confidence if est else None)
            st.record_success(printer.id, last_ok, f"{printer.brand} {printer.model} (démo)")
            for sp in specs:
                if sp.start is None:      # « il en reste » : aucune valeur chiffrée, donc ni échantillon ni alerte
                    st.upsert_supply(printer.id, Supply(sp.key, sp.description, sp.kind, sp.color, Status.SOME_REMAINING,
                                                        None, -3, 100, 19), 0, last_ok)
            for sp in specs:
                if sp.replace_day is not None:
                    events.append((t0 + sp.replace_day * DAY, printer.id, sp.key, EventType.REPLACED, 100.0,
                                   f"{sp.description} : remplacement détecté"))
            current = {sp.key: round(value_at(sp, (last_ok - t0) / DAY), 1) for sp in specs if sp.start is not None}
            for sp in specs:
                if sp.key not in current:
                    continue
                lv = level_of(current[sp.key])
                if lv:
                    events.append((last_ok - 2 * DAY, printer.id, sp.key,
                                   EventType.CRITICAL if lv == 2 else EventType.WARN, current[sp.key],
                                   f"{sp.description} : {current[sp.key]:g} % restant"))
            if down:
                for _ in range(3):
                    st.record_failure(printer.id, "Timeout: No Response (démo)")
                st.mark_down(printer.id)
                events.append((last_ok + 0.5 * DAY, printer.id, None, EventType.DOWN, None,
                               "Imprimante injoignable (3 échecs consécutifs)"))
        for ts, pid, key, etype, pct, msg in sorted(events, key=lambda e: e[0]):
            st.add_event(ts, pid, key, etype, pct, msg, notify=False)
    finally:
        st.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db", help="chemin de la base de démonstration à créer (ex. data/demo.db)")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--force", action="store_true", help="écraser un fichier existant")
    ap.add_argument("--profile", choices=("demo", "audit"), default="demo",
                    help="demo : 4 imprimantes ; audit : 18 imprimantes calquées sur l'audit du dossier technique")
    args = ap.parse_args()
    p = Path(args.db)
    if p.exists():
        if not args.force:
            print(f"{p} existe déjà : refus d'écraser (utiliser --force pour une base de démo).", file=sys.stderr)
            return 1
        for suffix in ("", "-wal", "-shm"):
            Path(str(p) + suffix).unlink(missing_ok=True)
    fleet = audit_fleet() if args.profile == "audit" else PRINTERS
    if args.profile == "audit":
        global WARN
        WARN = 30.0        # l'audit du dossier technique qualifie « attention » les niveaux de 25 à 30 % : seuil à 30 % dans demo-audit.toml
    seed(str(p), days=args.days, fleet=fleet)
    print(f"Base de démonstration créée : {p} ({len(fleet)} imprimantes fictives, {args.days} jours).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
