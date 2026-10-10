"""Correctifs issus de la revue de code : prévision persistée, consommables fantômes, garde Host, file de synchronisation,
CSV Excel strict, SMTP, purge espacée, durcissement de la lecture HP, commandes de la CLI."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from printwatch import ledm
from printwatch.alerts import Thresholds
from printwatch.cli import main
from printwatch.collector import Collector
from printwatch.config import CollectorConfig, ConfigError, SmtpConfig, load_printers
from printwatch.model import Printer, RawSupply
from printwatch.notify import SmtpNotifier
from printwatch.storage import Storage
from printwatch.sync import SyncController
from printwatch.web import ReadOnlyStorage, _hostname, create_app


class Clock:
    def __init__(self): self.t = 1_800_000_000.0
    def __call__(self): return self.t


class Notif:
    def send(self, events): pass


class Client:
    def __init__(self): self.level = 90
    def supplies(self, host): return [RawSupply("1.1", 3, 5, "Cyan Ink", 19, 100, self.level)]
    def sys_descr(self, host): return "EPSON"


def collector(clock, **cfg):
    st = Storage(":memory:")
    c = Collector(st, Client(), Thresholds(15, 5, 5), Notif(), CollectorConfig(retry_delay_s=0, **cfg), clock, sleep=lambda s: None)
    return c, st


# --- prévision persistée par le collecteur ---------------------------------------------------------------------------
def test_forecast_is_computed_once_per_cycle_and_read_without_history(tmp_path, monkeypatch):
    clock, path = Clock(), str(tmp_path / "f.db")
    st = Storage(path)
    c = Collector(st, Client(), Thresholds(15, 5, 5), Notif(), CollectorConfig(retry_delay_s=0), clock, sleep=lambda s: None)
    p = Printer("p1", "Compta", "10.0.0.1", "Epson")
    for day in range(8):                                                # 8 jours de consommation régulière : 90 % → 55 %
        c.client.level = 90 - 5 * day
        clock.t += 86400
        c.run_cycle([p])
    row = st.conn.execute("SELECT fc_days, fc_conf FROM supplies").fetchone()
    assert row["fc_days"] and 8 < row["fc_days"] < 14 and row["fc_conf"] in ("low", "medium", "high")
    st.close()

    def boom(*a, **k):
        raise AssertionError("l'API ne doit plus recalculer la prévision à chaque requête")
    monkeypatch.setattr(ReadOnlyStorage, "history", boom)
    fc = TestClient(create_app(path, clock=clock)).get("/api/status").json()["printers"][0]["supplies"][0]["forecast"]
    assert fc and fc["confidence"] == row["fc_conf"]


def test_old_database_is_migrated_and_readable_read_only(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    db = sqlite3.connect(path)                                          # base d'avant les colonnes user / fc_days / fc_conf
    db.executescript("""CREATE TABLE printers (id TEXT PRIMARY KEY, name TEXT NOT NULL, host TEXT NOT NULL, brand TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1, sys_descr TEXT, last_seen REAL,
        last_error TEXT, failures INTEGER NOT NULL DEFAULT 0, down INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE supplies (printer_id TEXT NOT NULL, key TEXT NOT NULL, description TEXT NOT NULL, kind TEXT NOT NULL, color TEXT,
        status TEXT NOT NULL, pct REAL, level INTEGER, max_capacity INTEGER, alert_level INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
        PRIMARY KEY (printer_id, key));
        INSERT INTO printers (id,name,host) VALUES ('a','A','h');
        INSERT INTO supplies VALUES ('a','1.1','Cyan','consumable','cyan','ok',50,50,100,0,1);""")
    db.commit(); db.close()
    assert TestClient(create_app(path)).get("/api/status").status_code == 200       # lecture seule : valeurs vides, aucun plantage
    st = Storage(path)                                                              # le collecteur migre
    assert {"fc_days", "fc_conf"} <= st._columns("supplies") and "user" in st._columns("printers")


# --- consommables fantômes ----------------------------------------------------------------------------------------
def test_ghost_supply_is_stale_and_cannot_keep_an_alert_alive(tmp_path):
    path = str(tmp_path / "g.db")
    st = Storage(path)
    with st.conn:
        st.conn.execute("INSERT INTO printers (id,name,host,last_seen) VALUES ('p','P','h',1000)")
        st.conn.execute("INSERT INTO supplies (printer_id,key,description,kind,color,status,pct,alert_level,updated_at) VALUES ('p','1.1','Cyan','consumable','cyan','ok',80,0,1000)")
        st.conn.execute("INSERT INTO supplies (printer_id,key,description,kind,color,status,pct,alert_level,updated_at) VALUES ('p','9.9','Ancienne','consumable','black','ok',2,2,10)")
    st.close()
    p = TestClient(create_app(path)).get("/api/status").json()["printers"][0]
    assert {s["key"]: s["stale"] for s in p["supplies"]} == {"1.1": False, "9.9": True}
    assert p["state"] == "OK"                                                       # l'ancienne alerte critique ne compte plus


# --- garde « Host » (rebinding DNS) --------------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [("localhost:8080", "localhost"), ("127.0.0.1", "127.0.0.1"), ("[::1]:8080", "[::1]"),
                                          ("Evil.Example:80", "evil.example"), ("", "")])
def test_hostname_parsing(raw, expected):
    assert _hostname(raw) == expected


def test_host_guard_blocks_rebinding_but_allows_local_names(tmp_path):
    path = str(tmp_path / "h.db"); Storage(path).close()
    c = TestClient(create_app(path, manage="open", allowed_hosts=["localhost", "127.0.0.1", "[::1]"]))
    assert c.get("/api/status", headers={"Host": "localhost:8080"}).status_code == 200
    assert c.get("/api/status", headers={"Host": "[::1]:8080"}).status_code == 200
    r = c.post("/api/printers", json={"name": "X", "host": "10.0.0.1"}, headers={"Host": "attaquant.example:8080", "Origin": "http://attaquant.example:8080"})
    assert r.status_code == 400                                                     # origine « cohérente » mais nom étranger : refusé
    assert TestClient(create_app(path)).get("/api/status", headers={"Host": "n-importe-quoi"}).status_code == 200   # sans liste : pas de contrôle


# --- file d'attente de synchronisation ----------------------------------------------------------------------------
def test_sync_can_queue_a_second_cycle_while_one_is_running():
    ctl = SyncController(clock=lambda: 1e6)
    with ctl.cycle():
        assert ctl.request() == "running" and not ctl._wake.is_set()                # simple clic : rien n'est mis en file
        assert ctl.request(queue=True) == "queued" and ctl._wake.is_set()           # ajout d'imprimante : un cycle suivra aussitôt
    stop = threading.Event()
    t0 = time.monotonic()
    ctl.wait(30, stop)                                                              # le cycle en file démarre sans attendre l'intervalle
    assert time.monotonic() - t0 < 1 and not ctl._wake.is_set()


def test_sync_wait_ends_on_timeout_and_on_stop():
    ctl = SyncController()
    stop = threading.Event(); t0 = time.monotonic(); ctl.wait(0.2, stop)
    assert 0.15 < time.monotonic() - t0 < 1.5
    stop.set(); t0 = time.monotonic(); ctl.wait(30, stop)
    assert time.monotonic() - t0 < 1


# --- CSV : booléens Excel, valeurs invalides, doublons --------------------------------------------------------------
def test_csv_accepts_excel_booleans_and_rejects_typos_and_duplicate_hosts(tmp_path):
    f = tmp_path / "p.csv"
    f.write_text("id;host;enabled\na;10.0.0.1;VRAI\nb;10.0.0.2;FAUX\nc;10.0.0.3;\n", encoding="utf-8")
    assert [p.enabled for p in load_printers(f)] == [True, False, True]
    f.write_text("id,host,enabled\na,10.0.0.1,peut-etre\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="enabled"):
        load_printers(f)
    f.write_text("id,host\na,10.0.0.1\nb,10.0.0.1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="adresse en double"):
        load_printers(f)


# --- SMTP --------------------------------------------------------------------------------------------------------------
class FakeSmtp:
    calls: list = []

    def __init__(self, host, port, timeout=None, context=None):
        FakeSmtp.calls.append(("connect", type(self).__name__, host, port))
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def starttls(self, context=None): FakeSmtp.calls.append(("starttls",))
    def login(self, u, p): FakeSmtp.calls.append(("login", u, p))
    def send_message(self, m): FakeSmtp.calls.append(("send", m["Subject"]))


class FakeSmtpSsl(FakeSmtp):
    pass


EVENTS = [{"type": "critical", "printer_name": "P", "host": "h", "message": "Cyan : 1 % restant", "ts": 1.0, "location": ""}]


@pytest.mark.parametrize("port,starttls,expected", [(25, False, ["FakeSmtp"]), (587, True, ["FakeSmtp", "starttls"]), (465, True, ["FakeSmtpSsl"])])
def test_smtp_transport_selection(monkeypatch, port, starttls, expected):
    FakeSmtp.calls = []
    monkeypatch.setattr("smtplib.SMTP", FakeSmtp); monkeypatch.setattr("smtplib.SMTP_SSL", FakeSmtpSsl)
    SmtpNotifier(SmtpConfig(host="mx", port=port, starttls=starttls, recipients=["a@b.c"])).send(EVENTS)
    steps = [c[1] if c[0] == "connect" else c[0] for c in FakeSmtp.calls if c[0] != "send"]
    assert steps == expected and FakeSmtp.calls[-1][0] == "send"


def test_smtp_login_requires_the_password_variable(monkeypatch):
    FakeSmtp.calls = []
    monkeypatch.setattr("smtplib.SMTP", FakeSmtp)
    cfg = SmtpConfig(host="mx", port=25, user="u", recipients=["a@b.c"])
    monkeypatch.delenv("PRINTWATCH_SMTP_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="PRINTWATCH_SMTP_PASSWORD"):
        SmtpNotifier(cfg).send(EVENTS)
    monkeypatch.setenv("PRINTWATCH_SMTP_PASSWORD", "s3cret")
    SmtpNotifier(cfg).send(EVENTS)
    assert ("login", "u", "s3cret") in FakeSmtp.calls


# --- purge espacée -------------------------------------------------------------------------------------------------------
def test_purge_runs_at_most_once_per_hour():
    clock = Clock()
    c, st = collector(clock)
    calls = []
    orig = st.purge
    st.purge = lambda before: calls.append(before) or orig(before)
    p = Printer("p1", "P", "10.0.0.1")
    for minutes in (0, 10, 20, 70, 80):
        clock.t = 1_800_000_000.0 + minutes * 60
        c.run_cycle([p])
    assert len(calls) == 2                                                          # cycles de 0 et de 70 minutes


# --- lecture HP : redirections et lenteur ----------------------------------------------------------------------------------
def _server(handler):
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_ledm_refuses_redirects_to_another_host():
    hits = []

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path); self.send_response(200); self.end_headers(); self.wfile.write(b"<x/>")
        def log_message(self, *a): pass
    target = _server(Target)

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302); self.send_header("Location", f"http://localhost:{target.server_port}/interne"); self.end_headers()
        def log_message(self, *a): pass
    src = _server(Redirect)
    try:
        with pytest.raises(ledm.LedmError, match="autre hôte"):
            ledm.fetch(f"127.0.0.1:{src.server_port}", timeout=2)
        assert hits == []                                                           # la cible interne n'a jamais été contactée
    finally:
        src.shutdown(); target.shutdown()


def test_ledm_cuts_a_trickling_response():
    class Slow(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Length", "100000"); self.end_headers()
            try:
                for _ in range(50):
                    self.wfile.write(b"x" * 100); self.wfile.flush(); time.sleep(0.1)
            except OSError:
                pass
        def log_message(self, *a): pass
    srv = _server(Slow)
    try:
        t0 = time.monotonic()
        with pytest.raises(ledm.LedmError, match="trop lente"):
            ledm.fetch(f"127.0.0.1:{srv.server_port}", timeout=2, deadline_s=0.5)
        assert time.monotonic() - t0 < 3
    finally:
        srv.shutdown()


# --- commandes de la CLI (jusqu'ici non testées) ------------------------------------------------------------------------------
@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    db = tmp_path / "c.db"
    st = Storage(str(db))
    st.upsert_printer(Printer("a", "Pool A", "10.0.0.1", "EPSON", "WF", "BAT A", "Awa"))
    st.upsert_printer(Printer("b", "Pool B", "10.0.0.2", "HP", "M479", "", ""))
    st.conn.execute("INSERT INTO supplies (printer_id,key,description,kind,color,status,pct,alert_level,updated_at) VALUES ('a','1.1','Cyan Ink','consumable','cyan','ok',3,2,1)")
    st.conn.commit(); st.close()
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'[snmp]\nversion = "2c"\n[collector]\ndb_path = "{db.as_posix()}"\n', encoding="utf-8")
    monkeypatch.setenv("PRINTWATCH_SNMP_COMMUNITY", "public")
    return str(cfg), tmp_path


def test_cli_status_json_events_and_table(cli_env, capsys):
    cfg, _ = cli_env
    assert main(["-c", cfg, "status", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert any(r["printer"] == "Pool A" and r["alert"] == "CRIT" for r in rows)
    assert main(["-c", cfg, "status"]) == 0 and "Pool A" in capsys.readouterr().out
    assert main(["-c", cfg, "events", "-n", "5"]) == 0


class FakeNet:
    def __init__(self, cfg): pass
    def supplies(self, host):
        if host == "10.0.0.2":
            from printwatch.snmp import SnmpError
            raise SnmpError("Timeout")
        return [RawSupply("1.1", 3, 5, "Cyan Ink", 19, 100, 42)]
    def sys_descr(self, host): return "EPSON test"


def test_cli_probe_and_matrix_use_the_database_list(cli_env, monkeypatch, capsys):
    cfg, tmp = cli_env
    monkeypatch.setattr("printwatch.cli.NetSnmpClient", FakeNet)
    assert main(["-c", cfg, "probe", "10.0.0.1"]) == 0 and "42 %" in capsys.readouterr().out
    assert main(["-c", cfg, "probe", "10.0.0.2"]) == 2                              # échec SNMP : code 2
    out = tmp / "m.csv"
    assert main(["-c", cfg, "matrix", "-o", str(out)]) == 0                         # sans -p : liste de la base
    text = out.read_text(encoding="utf-8")
    assert "Pool" not in text and "10.0.0" not in text                              # aucune adresse IP, identifiants seulement
    assert text.count("unreachable") == 1 and "42" in text
    assert "1/2 imprimantes joignables" in capsys.readouterr().err


def test_cli_rejects_a_host_that_would_be_read_as_an_option(cli_env, capsys):
    cfg, _ = cli_env
    assert main(["-c", cfg, "probe", "--", "-v3"]) != 0                            # « -- » : l'analyseur d'arguments laisse passer, check_host refuse
    assert "invalide" in capsys.readouterr().err
