from pathlib import Path

import pytest

from printwatch import ledm
from printwatch.model import RawSupply

FIX = Path(__file__).parent / "fixtures"


def raw(index, desc, level, klass=3, stype=None):
    return RawSupply(index, klass, stype, desc, 19, 100, level)


def test_parse_real_hp_m479_files():
    """Fichiers réels (HP Color LaserJet Pro M479) : .89 = noir compatible neuf + C/M/Y HP très bas ; .105 = tout très bas."""
    s89 = {s.color: s for s in ledm.parse((FIX / "hp89.xml").read_bytes())}
    assert (s89["black"].pct, s89["black"].state, s89["black"].brand) == (100, "nonHP", "clone")
    assert [(c, s89[c].pct, s89[c].state) for c in ("cyan", "magenta", "yellow")] == [("cyan", 0, "veryLow"), ("magenta", 0, "veryLow"), ("yellow", 0, "veryLow")]
    assert s89["magenta"].product == "W2033A"
    s105 = {s.color: (s.pct, s.state) for s in ledm.parse((FIX / "hp105.xml").read_bytes())}
    assert s105 == {"black": (0, "veryLow"), "cyan": (0, "veryLow"), "magenta": (1, "low"), "yellow": (0, "veryLow")}


def test_complete_fills_only_unknown_levels():
    data = ledm.parse((FIX / "hp105.xml").read_bytes())
    rows = [raw("1.1", "Black Cartridge HP W2030A", -2), raw("1.2", "Cyan Cartridge HP W2031A", -2),
            raw("1.3", "Magenta Cartridge HP W2033A", 7),        # valeur SNMP valide : jamais écrasée
            raw("1.4", "Yellow Cartridge HP W2032A", -3),        # « il en reste » : réponse valide, non touchée
            raw("1.5", "Waste toner box", -2, klass=4)]          # réceptacle : hors périmètre
    out = {r.index: r.level for r in ledm.complete(rows, data)}
    assert out == {"1.1": 0, "1.2": 0, "1.3": 7, "1.4": -3, "1.5": -2}


def test_complete_matches_by_color_when_reference_differs():
    data = ledm.parse((FIX / "hp89.xml").read_bytes())
    out = ledm.complete([raw("1.2", "Cyan Cartridge compatible XYZ", -2)], data)
    assert out[0].level == 0


def test_parse_rejects_entities_and_garbage():
    with pytest.raises(ledm.LedmError):
        ledm.parse(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>')
    with pytest.raises(ledm.LedmError):
        ledm.parse(b"<html>pas du xml")


def test_fetch_over_http_from_a_local_server():
    """Requête réelle (sans mock réseau) vers un faux serveur web d'imprimante qui sert le XML du .89."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    body = (FIX / "hp89.xml").read_bytes()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            ok = self.path == ledm.PATH
            self.send_response(200 if ok else 404)
            self.send_header("Content-Length", str(len(body) if ok else 0))
            self.end_headers()
            if ok:
                self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        got = ledm.fetch(f"127.0.0.1:{srv.server_port}", timeout=2)
        assert {s.color for s in got} == {"black", "cyan", "magenta", "yellow"}
    finally:
        srv.shutdown()


def test_fetch_failure_raises_ledm_error():
    with pytest.raises(ledm.LedmError):
        ledm.fetch("127.0.0.1:1", timeout=0.5)               # port fermé : HTTP puis HTTPS échouent proprement
