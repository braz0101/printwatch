"""Estimation de la date de rupture à partir de l'historique d'un consommable.

On isole le segment qui suit le dernier remplacement (bond vers le haut du pourcentage restant),
puis on ajuste une droite par moindres carrés sur la fenêtre récente. Une jauge par paliers
(imprimantes qui ne remontent que 0/25/50/75/100 %) donne une estimation grossière : d'où le champ `confidence`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

DAY = 86400.0
MIN_RATE = 0.05  # points de pourcentage par jour, en dessous : considéré comme « ne consomme pas »


@dataclass(frozen=True)
class Forecast:
    days_left: float
    rate_per_day: float   # points de pourcentage consommés par jour (valeur positive)
    points: int
    span_days: float
    confidence: str       # "low" | "medium" | "high"


def last_segment(history: Sequence[tuple[float, float]], jump: float) -> list[tuple[float, float]]:
    """Points postérieurs au dernier remplacement (hausse >= jump entre deux relevés consécutifs)."""
    pts = sorted(history)
    start = 0
    for i in range(1, len(pts)):
        if pts[i][1] - pts[i - 1][1] >= jump:
            start = i
    return pts[start:]


def estimate(
    history: Sequence[tuple[float, float]],
    *,
    replacement_jump: float = 20.0,
    window_days: float = 30.0,
    min_span_days: float = 2.0,
    min_points: int = 4,
) -> Forecast | None:
    """history : liste de (timestamp_epoch_s, pourcentage_restant). None si les données ne permettent pas d'estimer."""
    seg = last_segment(history, replacement_jump)
    if len(seg) < min_points:
        return None
    t_end = seg[-1][0]
    seg = [p for p in seg if p[0] >= t_end - window_days * DAY]
    if len(seg) < min_points:
        return None
    span = (seg[-1][0] - seg[0][0]) / DAY
    if span < min_span_days:
        return None

    x0 = seg[0][0]
    xs = [(t - x0) / DAY for t, _ in seg]
    ys = [p for _, p in seg]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    if slope > -MIN_RATE:
        return None

    rate = -slope
    days_left = max(ys[-1], 0.0) / rate
    drop = ys[0] - ys[-1]
    if span >= 21 and drop >= 20:
        confidence = "high"
    elif span >= 7 and drop >= 8:
        confidence = "medium"
    else:
        confidence = "low"
    return Forecast(days_left=days_left, rate_per_day=rate, points=n, span_days=span, confidence=confidence)
