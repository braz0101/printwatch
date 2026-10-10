import pytest

from printwatch.config import load_printers

ROWS = [["id", "name", "host", "location", "user"], ["imp-1", "Pool Trésorerie", "10.0.0.1", "Bâtiment Administration", "Awa EXEMPLE"]]


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp1252"])
def test_excel_exports_are_accepted(tmp_path, delimiter, encoding):
    f = tmp_path / "p.csv"
    f.write_bytes("\n".join(delimiter.join(r) for r in ROWS).encode(encoding))
    (p,) = load_printers(f)
    assert (p.id, p.name, p.location, p.user) == ("imp-1", "Pool Trésorerie", "Bâtiment Administration", "Awa EXEMPLE")


def test_header_case_and_spaces_are_tolerated_and_bad_header_is_a_clean_error(tmp_path):
    f = tmp_path / "p.csv"
    f.write_text(" ID ;Host\nimp-1;10.0.0.1\n", encoding="utf-8")
    assert load_printers(f)[0].host == "10.0.0.1"
    f.write_text("nom;ip\nx;10.0.0.1\n", encoding="utf-8")
    from printwatch.config import ConfigError
    with pytest.raises(ConfigError):
        load_printers(f)
