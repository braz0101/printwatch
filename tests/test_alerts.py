import pytest

from printwatch.alerts import Thresholds, next_level, transition
from printwatch.model import EventType, Level

TH = Thresholds(warn=15, crit=5, hysteresis=5)


def test_invalid_thresholds():
    with pytest.raises(ValueError):
        Thresholds(warn=5, crit=10)


@pytest.mark.parametrize("pct,expected", [(50, Level.OK), (15.1, Level.OK), (15, Level.WARN), (6, Level.WARN),
                                           (5, Level.CRIT), (0, Level.CRIT)])
def test_raw_levels(pct, expected):
    assert next_level(Level.OK, pct, TH) is expected


def test_degradation_is_immediate():
    assert next_level(Level.OK, 4, TH) is Level.CRIT


def test_hysteresis_keeps_warn_until_above_threshold_plus_margin():
    assert next_level(Level.WARN, 18, TH) is Level.WARN
    assert next_level(Level.WARN, 20, TH) is Level.WARN
    assert next_level(Level.WARN, 20.1, TH) is Level.OK


def test_crit_relaxes_to_warn_then_ok():
    assert next_level(Level.CRIT, 7, TH) is Level.CRIT
    assert next_level(Level.CRIT, 12, TH) is Level.WARN
    assert next_level(Level.CRIT, 60, TH) is Level.OK


def test_replacement_resets_without_hysteresis():
    assert next_level(Level.CRIT, 16, TH, replaced=True) is Level.OK


def test_transitions():
    assert transition(Level.OK, Level.WARN) is EventType.WARN
    assert transition(Level.OK, Level.CRIT) is EventType.CRITICAL
    assert transition(Level.WARN, Level.CRIT) is EventType.CRITICAL
    assert transition(Level.CRIT, Level.WARN) is None
    assert transition(Level.WARN, Level.OK) is EventType.RESOLVED
    assert transition(Level.CRIT, Level.OK) is EventType.RESOLVED
    assert transition(Level.WARN, Level.WARN) is None
