from printwatch.forecast import DAY, estimate, last_segment


def series(start_pct, per_day, days, step_h=6, t0=1_000_000.0):
    pts, t = [], 0.0
    while t <= days * DAY:
        pts.append((t0 + t, start_pct - per_day * t / DAY))
        t += step_h * 3600
    return pts


def test_linear_consumption():
    est = estimate(series(80, 2.0, 20))     # 80 -> 40 %, 2 points par jour
    assert est is not None
    assert abs(est.rate_per_day - 2.0) < 0.01
    assert abs(est.days_left - 20.0) < 0.2
    assert est.confidence == "medium"       # 20 jours d'historique, chute de 40 points


def test_high_confidence_needs_three_weeks():
    assert estimate(series(100, 1.5, 28)).confidence == "high"


def test_only_segment_after_replacement_counts():
    old = series(30, 3.0, 8, t0=0.0)                           # 30 -> 6 %
    new = series(100, 1.0, 10, t0=old[-1][0] + 6 * 3600)       # cartouche changée : 100 -> 90 %
    assert last_segment(old + new, jump=20.0)[0] == new[0]
    est = estimate(old + new)
    assert est is not None and abs(est.rate_per_day - 1.0) < 0.01


def test_not_enough_data():
    assert estimate(series(80, 2.0, 1)) is None            # moins de 2 jours
    assert estimate(series(80, 2.0, 20)[:3]) is None       # moins de 4 points


def test_flat_consumption_gives_no_estimate():
    assert estimate(series(80, 0.0, 20)) is None
