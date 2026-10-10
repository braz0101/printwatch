"""Agrégats de lecture pour le dashboard : séries rééchantillonnées, historique de disponibilité, synthèse quotidienne.

Fonctions pures (aucun accès base) : `web.py` les alimente avec des lignes SQLite en lecture seule.
Les seuils sont toujours passés en paramètre (ils viennent de la configuration), jamais codés ici.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Mapping, Sequence

DAY = 86400.0
Interval = tuple[float, float]


def group_samples(rows: Iterable[Mapping | Sequence]) -> dict[tuple[str, str], list[tuple[float, float]]]:
    """Regroupe des lignes (printer_id, key, ts, pct) par série, en conservant l'ordre chronologique reçu."""
    out: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for r in rows:
        out[(r[0], r[1])].append((r[2], r[3]))
    return out


def downsample(points: Sequence[tuple[float, float]], since: float, until: float, n: int) -> list[list[float]]:
    """Rééchantillonne en `n` seaux réguliers sur [since, until] : on garde le DERNIER relevé de chaque seau.

    Garder le dernier (et non la moyenne) préserve les sauts bruts, donc la visibilité d'un remplacement de
    cartouche. L'horodatage rendu est la fin du seau (plafonnée à `until`) : toutes les séries partagent la même
    grille, ce qui permet un survol synchronisé. Un seau sans relevé est omis (une imprimante injoignable laisse un trou).
    """
    if n < 1 or until <= since:
        return []
    width = (until - since) / n
    last: dict[int, float] = {}
    for ts, pct in points:
        if since <= ts <= until:
            last[min(n - 1, int((ts - since) / width))] = pct
    return [[round(min(until, since + (i + 1) * width)), round(v, 1)] for i, v in sorted(last.items())]


def replacements(points: Sequence[tuple[float, float]], jump: float) -> list[int]:
    """Horodatages où le niveau saute d'au moins `jump` points vers le haut (remplacement de cartouche)."""
    return [round(b[0]) for a, b in zip(points, points[1:]) if b[1] - a[1] >= jump]


def down_intervals(
    events: Iterable[Mapping | Sequence],
    printers: Iterable[Mapping | Sequence],
    since: float,
    until: float,
) -> dict[str, list[Interval]]:
    """Périodes d'injoignabilité par imprimante, reconstruites depuis les événements `down` / `up`.

    `events` : (printer_id, ts, type) triés par date. `printers` : (id, down, last_seen). Il n'existe pas de
    table de disponibilité : la précision est celle du collecteur (alerte après 3 échecs consécutifs, donc un
    retard de quelques cycles au début de l'indisponibilité). Si l'imprimante est actuellement marquée injoignable
    sans événement `down` conservé (purgé), la période commence à son dernier relevé réussi.
    """
    per: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for e in events:
        per[e[0]].append((e[1], e[2]))
    out: dict[str, list[Interval]] = {}
    for p in printers:
        pid, is_down, last_seen = p[0], bool(p[1]), p[2]
        start: float | None = None
        spans: list[Interval] = []
        for ts, typ in per.get(pid, []):
            if typ == "down" and start is None:
                start = ts
            elif typ == "up" and start is not None:
                spans.append((start, ts))
                start = None
        if is_down:
            spans.append((start if start is not None else (last_seen or since), until))
        # Un `down` sans `up` alors que l'imprimante n'est plus marquée injoignable : événement incohérent, ignoré.
        clipped = [(max(a, since), min(b, until)) for a, b in spans if min(b, until) > max(a, since)]
        out[pid] = clipped
    return out


def uptime_pct(spans: Sequence[Interval], since: float, until: float) -> float:
    total = until - since
    if total <= 0:
        return 100.0
    return round(100.0 * (1 - sum(b - a for a, b in spans) / total), 1)


def fleet_daily(
    rows: Iterable[Mapping | Sequence],
    printers: Sequence[Mapping | Sequence],
    spans: Mapping[str, Sequence[Interval]],
    now: float,
    days: int,
    warn: float,
    crit: float,
) -> dict:
    """Un état par imprimante et par jour (jours UTC), plus les totaux du parc.

    État d'un jour : `down` si l'imprimante est injoignable plus de la moitié du temps écoulé ce jour-là ; sinon
    le niveau d'après le pourcentage restant le plus bas relevé ce jour-là (mêmes comparaisons `<=` que les alertes) ;
    `NODATA` sans relevé chiffré.
    """
    today = int(now // DAY)
    first = today - days + 1
    low: dict[tuple[str, int], float] = {}
    for r in rows:
        d = int(r[2] // DAY) - first
        if 0 <= d < days:
            k = (r[0], d)
            low[k] = min(low.get(k, 101.0), r[3])

    def level(pct: float) -> str:
        return "CRIT" if pct <= crit else "WARN" if pct <= warn else "OK"

    day_starts = [(first + i) * DAY for i in range(days)]
    totals = [{"crit": 0, "warn": 0, "ok": 0, "down": 0, "nodata": 0} for _ in range(days)]
    out_printers = []
    for p in printers:
        pid = p[0]
        down_s = [0.0] * days
        for a, b in spans.get(pid, ()):
            for d in range(max(0, int(a // DAY) - first), min(days, int(b // DAY) - first + 1)):
                down_s[d] += max(0.0, min(b, day_starts[d] + DAY) - max(a, day_starts[d]))
        cells, mins = [], []
        for d in range(days):
            elapsed = max(1.0, min(DAY, now - day_starts[d]))
            m = low.get((pid, d))
            mins.append(None if m is None else round(m, 1))
            if down_s[d] > elapsed / 2:
                state = "down"
            elif m is not None:
                state = level(m)
            else:
                state = "NODATA"
            cells.append(state)
            totals[d]["nodata" if state == "NODATA" else state.lower()] += 1
        out_printers.append({"id": pid, "name": p[1], "states": cells, "min": mins,
                             "down_hours": [round(s / 3600.0, 1) for s in down_s]})
    return {"days": day_starts, "printers": out_printers, "totals": totals}
