import sqlite3

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from printwatch.storage import Storage  # noqa: E402
from printwatch.web import ReadOnlyStorage, create_app  # noqa: E402


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    st = Storage(str(path))
    with st.conn:
        st.conn.execute("INSERT INTO printers (id,name,host,brand,model,location) VALUES ('p1','Compta','h','Epson','WF','B12')")
        st.conn.execute("INSERT INTO printers (id,name,host,down) VALUES ('p2','Eteinte','h2',1)")
        st.conn.execute("INSERT INTO supplies (printer_id,key,description,kind,color,status,pct,alert_level,updated_at) "
                        "VALUES ('p1','1.1','Cyan <b>x</b>','consumable','cyan','ok',11,1,1)")
        st.conn.execute("INSERT INTO supplies (printer_id,key,description,kind,color,status,pct,alert_level,updated_at) "
                        "VALUES ('p1','1.2','Boite','receptacle','waste','ok',70,0,1)")
    st.close()
    return str(path)


def test_status_sorted_by_urgency_and_summary(db):
    data = TestClient(create_app(db)).get("/api/status").json()
    assert [p["id"] for p in data["printers"]] == ["p2", "p1"]            # injoignable d'abord
    cyan = data["printers"][1]["supplies"][0]
    assert (cyan["pct"], cyan["alert"]) == (11, "WARN")
    assert data["summary"] == {"printers": 2, "down": 1, "crit": 0, "warn": 1, "ok": 0, "nodata": 0}
    assert [p["state"] for p in data["printers"]] == ["down", "WARN"]
    assert data["thresholds"] == {"warn": 15.0, "crit": 5.0}


def test_history_events_and_index(db):
    c = TestClient(create_app(db))
    assert c.get("/api/history/p1/1.1").json() == []
    assert c.get("/api/events").json() == []
    assert "PrintWatch" in c.get("/").text


def test_missing_db_gives_503(tmp_path):
    assert TestClient(create_app(str(tmp_path / "nope.db"))).get("/api/status").status_code == 503


def test_connection_is_read_only(db):
    ro = ReadOnlyStorage(db)
    with pytest.raises(sqlite3.OperationalError):
        ro.conn.execute("DELETE FROM printers")


def test_down_printer_has_no_forecast_and_is_flagged_stale(db):
    st = Storage(db)
    with st.conn:
        st.conn.execute("INSERT INTO supplies (printer_id,key,description,kind,color,status,pct,alert_level,updated_at) "
                        "VALUES ('p2','1.1','Black','consumable','black','ok',60,0,1)")
        for i in range(10):                       # historique suffisant pour qu'une prévision existe
            st.conn.execute("INSERT INTO samples (printer_id,key,ts,pct) VALUES ('p2','1.1',?,?)",
                            (__import__("time").time() - (10 - i) * 86400, 80 - 2 * i))
    st.close()
    down = next(p for p in TestClient(create_app(db)).get("/api/status").json()["printers"] if p["id"] == "p2")
    assert down["down"] and down["supplies"][0]["forecast"] is None and down["supplies"][0]["stale"] is True


def _add_supply(db, pid, key, pct, level):
    st = Storage(db)
    with st.conn:
        st.conn.execute("INSERT INTO supplies (printer_id,key,description,kind,color,status,pct,alert_level,updated_at) "
                        "VALUES (?,?,?,'consumable',NULL,'ok',?,?,1)", (pid, key, f"S{key}", pct, level))
    st.close()


def test_summary_counts_printers_not_supplies(db):
    """Deux consommables en alerte sur la même imprimante = UNE imprimante en attention (unité du badge)."""
    _add_supply(db, "p1", "9.9", 12, 1)                                   # p1 : 2 avertissements (11 % et 12 %)
    st = Storage(db)
    with st.conn:
        for pid, name in (("p3", "Crit"), ("p4", "Ok"), ("p5", "Vide")):
            st.conn.execute("INSERT INTO printers (id,name,host) VALUES (?,?,?)", (pid, name, "h"))
    st.close()
    _add_supply(db, "p3", "1.1", 3, 2)
    _add_supply(db, "p4", "1.1", 80, 0)
    _add_supply(db, "p2", "1.1", 2, 2)                                    # injoignable avec valeur périmée critique
    data = TestClient(create_app(db)).get("/api/status").json()
    states = {p["id"]: p["state"] for p in data["printers"]}
    assert states == {"p1": "WARN", "p2": "down", "p3": "CRIT", "p4": "OK", "p5": "NODATA"}
    s = data["summary"]
    assert s == {"printers": 5, "down": 1, "crit": 1, "warn": 1, "ok": 1, "nodata": 1}
    assert s["down"] + s["crit"] + s["warn"] + s["ok"] + s["nodata"] == s["printers"]


def test_user_column_csv_migration_and_api(tmp_path):
    """La colonne `user` est lue du CSV, migrée sur une ancienne base et exposée par /api/status."""
    import sqlite3
    from printwatch.config import load_printers
    from printwatch.storage import Storage

    csv = tmp_path / "p.csv"
    csv.write_text("id,name,host,location,utilisateur\np1,Pool X,10.0.0.1,Bâtiment Administration,Awa EXEMPLE\np2,Pool Y,10.0.0.2,,\n", encoding="utf-8")
    printers = load_printers(csv)
    assert [p.user for p in printers] == ["Awa EXEMPLE", ""]

    db = tmp_path / "old.db"
    old = sqlite3.connect(db)                                    # base d'avant la colonne `user`
    old.executescript("CREATE TABLE printers (id TEXT PRIMARY KEY, name TEXT NOT NULL, host TEXT NOT NULL, brand TEXT NOT NULL DEFAULT '', "
                      "model TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1, sys_descr TEXT, "
                      "last_seen REAL, last_error TEXT, failures INTEGER NOT NULL DEFAULT 0, down INTEGER NOT NULL DEFAULT 0);")
    old.commit(); old.close()
    st = Storage(str(db))
    for p in printers:
        st.upsert_printer(p)
    rows = {r["name"]: r["user"] for r in st.overview()}
    assert rows == {"Pool X": "Awa EXEMPLE", "Pool Y": ""}
    st.close()


def test_collector_health_states(db):
    """ok / stale / never / all_failed selon le dernier cycle enregistré et l'horloge."""
    from printwatch.storage import Storage as S
    base = 1_900_000_000.0
    state = lambda now: TestClient(create_app(db, interval_s=1800, clock=lambda: now)).get("/api/status").json()["collector"]  # noqa: E731
    assert state(base)["state"] == "never"
    st = S(db)
    st.record_cycle(base, polled=2, failed=0, events=0, duration=3.0)
    st.close()
    assert state(base + 600) == {"last_cycle": base, "polled": 2, "failed": 0, "interval_s": 1800, "state": "ok"}
    assert state(base + 2 * 1800 + 121)["state"] == "stale"       # collecteur arrêté depuis plus de 2 cycles
    st = S(db)
    st.record_cycle(base + 1800, polled=2, failed=2, events=0, duration=12.0)
    st.close()
    assert state(base + 1900)["state"] == "all_failed"            # il tourne, mais plus aucune imprimante ne répond
