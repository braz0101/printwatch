"""Fiche matérielle : décodage des sorties net-snmp réelles (Epson WF-C879R, HP M479fdn), stockage, collecte et API."""
import subprocess

import pytest

from printwatch.alerts import Thresholds
from printwatch.collector import Collector
from printwatch.config import CollectorConfig, SnmpConfig
from printwatch.device import TABLES, decode_error_state, normalize_mac, parse_device
from printwatch.model import Printer, RawSupply
from printwatch.snmp import NetSnmpClient
from printwatch.storage import Storage

# Sorties capturées sur le parc (snmpget -On -Oq -Ot, SNMPv1 ; l'OID inexistant est simplement absent).
EPSON_SCALARS = """\
.1.3.6.1.2.1.1.5.0 EPSON-BATADMIN
.1.3.6.1.2.1.1.6.0
.1.3.6.1.2.1.1.4.0
.1.3.6.1.2.1.1.3.0 71053900
.1.3.6.1.2.1.43.5.1.1.17.1 "X6GP003770"
.1.3.6.1.2.1.43.10.2.1.4.1.1 33848
.1.3.6.1.2.1.25.3.2.1.5.1 2
.1.3.6.1.2.1.25.3.5.1.1.1 3
.1.3.6.1.2.1.25.3.2.1.3.1 EPSON WF-C879R Series
"""
EPSON_TABLES = {
    "display": '.1.3.6.1.2.1.43.16.5.1.2.1.1 "Ready"\n.1.3.6.1.2.1.43.16.5.1.2.1.2 ""\n',
    "trays": """\
.1.3.6.1.2.1.43.8.2.1.9.1.1 150
.1.3.6.1.2.1.43.8.2.1.9.1.2 250
.1.3.6.1.2.1.43.8.2.1.10.1.1 0
.1.3.6.1.2.1.43.8.2.1.10.1.2 -3
.1.3.6.1.2.1.43.8.2.1.12.1.1 ""
.1.3.6.1.2.1.43.8.2.1.13.1.1 "Rear Auto Sheet Feeder"
.1.3.6.1.2.1.43.8.2.1.13.1.2 "Sheet feeder bin 1"
""",
    "covers": '.1.3.6.1.2.1.43.6.1.1.2.1.1 "Front Cover"\n.1.3.6.1.2.1.43.6.1.1.3.1.1 4\n',
    "alerts": "",
    "mac": ".1.3.6.1.2.1.2.2.1.6.1 38:9d:92:e1:e8:13\n",
}
HP_ALERTS = """\
.1.3.6.1.2.1.43.18.1.1.2.1.1 4
.1.3.6.1.2.1.43.18.1.1.2.1.2 4
.1.3.6.1.2.1.43.18.1.1.4.1.1 10
.1.3.6.1.2.1.43.18.1.1.4.1.2 10
.1.3.6.1.2.1.43.18.1.1.7.1.1 1102
.1.3.6.1.2.1.43.18.1.1.7.1.2 1102
.1.3.6.1.2.1.43.18.1.1.8.1.1 "66085"
.1.3.6.1.2.1.43.18.1.1.8.1.2 "66085"
"""


def test_epson_sheet_is_fully_decoded():
    info = parse_device(EPSON_SCALARS, ".1.3.6.1.2.1.25.3.5.1.2.1 00 \n", EPSON_TABLES)
    assert info["serial"] == "X6GP003770" and info["model"] == "EPSON WF-C879R Series"
    assert info["page_count"] == 33848 and info["uptime_s"] == 710539
    assert (info["device_status"], info["printer_status"]) == ("running", "idle")
    assert info["errors"] == [] and info["display"] == ["Ready"]
    assert info["mac"] == "38:9D:92:E1:E8:13"
    assert info["trays"] == [{"name": "Rear Auto Sheet Feeder", "media": None, "level": 0, "max": 150},
                             {"name": "Sheet feeder bin 1", "media": None, "level": -3, "max": 250}]
    assert info["covers"] == [{"name": "Front Cover", "status": "closed"}]
    assert "sys_location" not in info                                   # valeur vide : omise, pas une chaîne vide


def test_hp_duplicate_alerts_are_merged():
    info = parse_device(EPSON_SCALARS, "", {"alerts": HP_ALERTS})
    assert info["alerts"] == [{"severity": "warning", "group": 10, "code": 1102, "description": "66085"}]


@pytest.mark.parametrize("raw, flags", [
    ("00 ", []),
    ("20 ", ["lowToner"]),                                   # HP au magenta bas : bit 2
    ('"84 04 "', ["lowPaper", "jammed", "inputTrayEmpty"]),
    ("Hex-STRING: 08 ", ["doorOpen"]),
    ("", []),
])
def test_error_state_bits(raw, flags):
    assert decode_error_state(raw) == flags


def test_mac_normalisation():
    assert normalize_mac("4:e:3c:e9:a:cb") == "04:0E:3C:E9:0A:CB"
    assert normalize_mac("") is None and normalize_mac("0:0:0:0:0:0") is None


def test_partial_v1_response_is_accepted():
    """SNMPv1 : un OID absent donne un code de retour 2, mais net-snmp renvoie les autres valeurs ; on les garde."""
    def runner(cmd, **kw):
        if "-Ot" in cmd:
            return subprocess.CompletedProcess(cmd, 2, EPSON_SCALARS, "Error in packet\nReason: (noSuchName)")
        if cmd[-1] == TABLES["mac"]:
            return subprocess.CompletedProcess(cmd, 0, EPSON_TABLES["mac"], "")
        return subprocess.CompletedProcess(cmd, 1, "", "Timeout")
    client = NetSnmpClient(SnmpConfig(version="2c"), runner=runner, secrets={"community": "public"})
    info = client.device_info("10.0.0.5")
    assert info["serial"] == "X6GP003770" and info["mac"] == "38:9D:92:E1:E8:13" and info["trays"] == []


def test_storage_keeps_sheet_and_page_volume():
    st = Storage(":memory:")
    st.upsert_printer(Printer("p1", "A", "h"))
    st.set_device_info("p1", {"serial": "S1", "page_count": 1000}, 100.0)
    st.set_device_info("p1", {"serial": "S1", "page_count": 1250}, 200.0)
    assert st.device_infos()["p1"] == {"serial": "S1", "page_count": 1250, "updated_at": 200.0}
    assert st.pages_since(0) == {"p1": 250}
    assert st.pages_since(150) == {"p1": 0}
    st.set_device_info("p1", {"page_count": 10}, 300.0)                 # compteur qui recule : volume incalculable
    assert st.pages_since(0) == {}


class _Client:
    def __init__(self, info):
        self.info = info

    def supplies(self, host):
        return [RawSupply("1.1", 3, 5, "Black Ink", 19, 100, 50)]

    def sys_descr(self, host):
        return "X"

    def device_info(self, host):
        if isinstance(self.info, Exception):
            raise self.info
        return self.info


class _Notifier:
    def send(self, events):
        pass


@pytest.mark.parametrize("info, expected", [({"serial": "S9", "page_count": 7}, {"serial": "S9", "page_count": 7}),
                                            (RuntimeError("table absente"), None)])
def test_collector_stores_sheet_and_never_fails_on_it(info, expected):
    st = Storage(":memory:")
    col = Collector(st, _Client(info), Thresholds(15, 5, 5), _Notifier(), CollectorConfig(retry_delay_s=0), lambda: 1000.0)
    r = col.run_cycle([Printer("p1", "A", "10.0.0.5")])
    assert r.failed == 0                                                 # la fiche est facultative
    got = st.device_infos().get("p1")
    assert (got and {k: v for k, v in got.items() if k != "updated_at"}) == expected


def test_status_api_exposes_sheet_and_pages(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from printwatch.web import create_app

    path = str(tmp_path / "t.db")
    st = Storage(path)
    st.upsert_printer(Printer("p1", "A", "h"))
    st.upsert_printer(Printer("p2", "B", "h2"))
    st.set_device_info("p1", {"serial": "S1", "page_count": 100}, 1000.0)
    st.set_device_info("p1", {"serial": "S1", "page_count": 160}, 2000.0)
    st.close()
    data = TestClient(create_app(path, clock=lambda: 3000.0)).get("/api/status").json()
    by_id = {p["id"]: p for p in data["printers"]}
    assert by_id["p1"]["info"]["serial"] == "S1" and by_id["p1"]["pages_30d"] == 60
    assert by_id["p2"]["info"] is None and by_id["p2"]["pages_30d"] is None


@pytest.mark.parametrize("ds, ps", [("2", "3"), ("running", "idle"), ("running(2)", "idle(3)")])
def test_enums_accept_codes_and_names(ds, ps):
    """net-snmp 5.5 pour Windows charge HOST-RESOURCES-MIB malgré `-m ""` : il écrit alors le nom au lieu du code."""
    out = EPSON_SCALARS.replace(".25.3.2.1.5.1 2", f".25.3.2.1.5.1 {ds}").replace(".25.3.5.1.1.1 3", f".25.3.5.1.1.1 {ps}")
    info = parse_device(out, "", {})
    assert (info["device_status"], info["printer_status"]) == ("running", "idle")
