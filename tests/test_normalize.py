import pytest

from printwatch.model import Kind, RawSupply, Status
from printwatch.normalize import guess_color, normalize


def raw(level, cap=100, klass=3, stype=None, desc="Black Ink", unit=None):
    return RawSupply("1.1", klass, stype, desc, unit, cap, level)


def test_consumable_percentage():
    s = normalize(raw(37, 100))
    assert s.kind is Kind.CONSUMABLE and s.status is Status.OK and s.pct_remaining == 37.0


def test_capacity_other_than_100():
    assert normalize(raw(1500, 6000)).pct_remaining == 25.0


def test_receptacle_is_inverted():
    s = normalize(raw(90, 100, klass=4, desc="Maintenance Box"))
    assert s.kind is Kind.RECEPTACLE and s.pct_remaining == 10.0 and s.color == "waste"


def test_receptacle_detected_by_type_even_if_class_is_wrong():
    assert normalize(raw(80, 100, klass=3, stype=4)).kind is Kind.RECEPTACLE


@pytest.mark.parametrize("level,status", [(-1, Status.OTHER), (-2, Status.UNKNOWN), (-3, Status.SOME_REMAINING),
                                           (-9, Status.INVALID), (None, Status.INVALID)])
def test_special_values_have_no_percentage(level, status):
    s = normalize(raw(level))
    assert s.status is status and s.pct_remaining is None


@pytest.mark.parametrize("cap", [None, 0, -2])
def test_missing_capacity(cap):
    assert normalize(raw(40, cap)).status is Status.NO_CAPACITY


def test_percent_unit_without_capacity():
    s = normalize(raw(40, None, unit=19))
    assert s.status is Status.OK and s.pct_remaining == 40.0


def test_level_above_capacity_is_clamped():
    assert normalize(raw(130, 100)).pct_remaining == 100.0


@pytest.mark.parametrize("text,color", [("Black Ink Cartridge", "black"), ("Cartouche Jaune", "yellow"),
                                         ("Cyan toner", "cyan"), ("Magenta", "magenta"),
                                         ("Maintenance Box", "waste"), ("Fuser kit", None)])
def test_color_guess(text, color):
    assert guess_color(text) == color
