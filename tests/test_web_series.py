"""Endpoints d'agrégats du dashboard : séries du parc, disponibilité, synthèse quotidienne. Horloge fixe pour des tests déterministes."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from printwatch.series import DAY, down_intervals, downsample, replacements, uptime_pct  # noqa: E402
from printwatch.storage import Storage  # noqa: E402
from printwatch.web import create_app  # noqa: E402

NOW = 1_800_000_000.0 - (1_800_000_000.0 % DAY) + 12 * 3600      # midi UTC d'un jour donné


def _db(tmp_path):
    path = tmp_path / "t.db"
    st = Storage(str(path))
    with st.conn:
        st.conn.execute("INSERT INTO printers (id,name,host,last_seen) VALUES ('p1','Alpha','h1',?)", (NOW,))
        st.conn.execute("INSERT INTO printers (id,name,host,last_seen) VALUES ('p2','Bravo','h2',?)", (NOW,))
        st.conn.execute("INSERT INTO printers (id,name,host,down,last_seen) VALUES ('p3','Charlie','h3',1,?)", (NOW - 2 * DAY,))
    return st, str(path)


def _sample(st, pid, key, ts, pct):
    st.conn.execute("INSERT INTO samples (printer_id,key,ts,pct) VALUES (?,?,?,?)", (pid, key, ts, pct))


def _event(st, pid, ts, typ):
    st.conn.execute("INSERT INTO events (ts,printer_id,type,message,notified) VALUES (?,?,?,?,1)", (ts, pid, typ, typ))


def client(path, **kw):
    return TestClient(create_app(path, clock=lambda: NOW, **kw))


# --------------------------------------------------------------------------------- fonctions pures
def test_downsample_keeps_last_value_per_bucket_and_skips_empty_buckets():
    since, until = 0.0, 100.0
    pts = [(1, 90.0), (9, 80.0), (55, 40.0)]            # seaux de 10 s : deux relevés dans le 1er, un dans le 6e
    out = downsample(pts, since, until, 10)
    assert out == [[10, 80.0], [60, 40.0]]              # dernier relevé du seau, fin de seau, seaux vides omis
    assert downsample(pts, since, until, 0) == [] and downsample(pts, 5, 5, 10) == []


def test_replacements_detects_upward_jump_only():
    pts = [(1.0, 50.0), (2.0, 40.0), (3.0, 100.0), (4.0, 95.0), (5.0, 110.0 - 5)]
    assert replacements(pts, 20.0) == [3]                # +60 : remplacement ; +10 : bruit


def test_down_intervals_closed_open_clipped_and_inconsistent_events():
    since, until = 100.0, 1000.0
    events = [("a", 50.0, "down"), ("a", 200.0, "up"), ("a", 500.0, "down"), ("a", 600.0, "up"),
              ("b", 700.0, "down"),                      # b est marqué injoignable : période ouverte jusqu'à `until`
              ("c", 300.0, "down")]                      # c n'est plus marqué injoignable : événement incohérent, ignoré
    printers = [("a", 0, 900.0), ("b", 1, 700.0), ("c", 0, 900.0), ("d", 1, 400.0)]
    spans = down_intervals(events, printers, since, until)
    assert spans["a"] == [(100.0, 200.0), (500.0, 600.0)]     # la 1re période est tronquée au début de la fenêtre
    assert spans["b"] == [(700.0, 1000.0)]
    assert spans["c"] == []
    assert spans["d"] == [(400.0, 1000.0)]                    # marqué injoignable sans événement : depuis le dernier relevé
    assert uptime_pct(spans["a"], since, until) == pytest.approx(77.8, abs=0.05)
    assert uptime_pct([], since, until) == 100.0


# --------------------------------------------------------------------------------- /api/series
def test_series_returns_whole_fleet_on_a_common_grid(tmp_path):
    st, path = _db(tmp_path)
    with st.conn:
        since = NOW - 10 * DAY
        for i in range(10):                                   # p1/1.1 : 1 relevé par jour (au milieu du seau), avec remplacement
            _sample(st, "p1", "1.1", since + (i + 0.5) * DAY, [80, 70, 60, 50, 100, 90, 80, 70, 60, 50][i])
        _sample(st, "p2", "1.1", since + 9.5 * DAY, 33.0)
    st.close()
    data = client(path).get("/api/series?days=10&points=10").json()
    assert data["until"] == NOW and data["since"] == NOW - 10 * DAY and data["step"] == DAY
    by = {(s["printer"], s["key"]): s for s in data["series"]}
    assert set(by) == {("p1", "1.1"), ("p2", "1.1")}          # tout le parc en une requête
    p1 = by[("p1", "1.1")]
    assert [p[1] for p in p1["pts"]] == [80.0, 70.0, 60.0, 50.0, 100.0, 90.0, 80.0, 70.0, 60.0, 50.0]
    assert p1["replaced"] == [round(since + 4.5 * DAY)]       # saut de 50 à 100 : un seul remplacement
    ends = {p[0] for s in data["series"] for p in s["pts"]}
    assert all((e - data["since"]) % DAY == 0 for e in ends)   # grille commune : fins de seau


def test_series_filter_by_printer_and_validation(tmp_path):
    st, path = _db(tmp_path)
    with st.conn:
        _sample(st, "p1", "1.1", NOW - DAY, 50)
        _sample(st, "p2", "1.1", NOW - DAY, 60)
    st.close()
    c = client(path)
    only = c.get("/api/series?printer=p2").json()["series"]
    assert [(s["printer"], s["key"]) for s in only] == [("p2", "1.1")]
    assert c.get("/api/series?printer=inconnue").json()["series"] == []
    assert c.get("/api/series?days=0").status_code == 422
    assert c.get("/api/series?days=401").status_code == 422
    assert c.get("/api/series?points=7").status_code == 422
    assert c.get("/api/series?points=401").status_code == 422


# --------------------------------------------------------------------------------- /api/availability
def test_availability_periods_uptime_and_current_state(tmp_path):
    st, path = _db(tmp_path)
    with st.conn:
        _event(st, "p1", NOW - 10 * DAY, "down")
        _event(st, "p1", NOW - 8 * DAY, "up")                 # p1 : 2 jours d'indisponibilité sur 20
        _event(st, "p3", NOW - 2 * DAY, "down")               # p3 : toujours injoignable
    st.close()
    data = client(path).get("/api/availability?days=20").json()
    by = {p["id"]: p for p in data["printers"]}
    assert by["p1"]["down"] == [[round(NOW - 10 * DAY), round(NOW - 8 * DAY)]] and by["p1"]["uptime_pct"] == 90.0
    assert by["p1"]["down_now"] is False
    assert by["p2"]["down"] == [] and by["p2"]["uptime_pct"] == 100.0
    assert by["p3"]["down_now"] is True and by["p3"]["down"] == [[round(NOW - 2 * DAY), round(NOW)]]
    assert by["p3"]["uptime_pct"] == 90.0
    assert [p["name"] for p in data["printers"]] == ["Alpha", "Bravo", "Charlie"]


# --------------------------------------------------------------------------------- /api/fleet/daily
def test_fleet_daily_states_use_configured_thresholds_and_totals_add_up(tmp_path):
    st, path = _db(tmp_path)
    with st.conn:
        _sample(st, "p1", "1.1", NOW - DAY, 28.0)             # hier : 28 %
        _sample(st, "p1", "1.2", NOW - DAY, 90.0)
        _sample(st, "p1", "1.1", NOW, 4.0)                    # aujourd'hui : 4 %
        _sample(st, "p2", "1.1", NOW, 28.0)
        _event(st, "p3", NOW - 2 * DAY, "down")
    st.close()
    app30 = client(path, warn=30.0, crit=5.0).get("/api/fleet/daily?days=3").json()
    by = {p["id"]: p for p in app30["printers"]}
    assert app30["thresholds"] == {"warn": 30.0, "crit": 5.0} and len(app30["days"]) == 3
    assert by["p1"]["states"] == ["NODATA", "WARN", "CRIT"]    # le plus bas du jour : 28 % -> attention, 4 % -> critique
    assert by["p1"]["min"] == [None, 28.0, 4.0]
    assert by["p2"]["states"] == ["NODATA", "NODATA", "WARN"]
    assert by["p3"]["states"] == ["NODATA", "down", "down"]    # injoignable depuis l'avant-veille midi
    assert all(sum(t.values()) == 3 for t in app30["totals"])  # chaque jour : une catégorie par imprimante
    assert app30["totals"][-1] == {"crit": 1, "warn": 1, "ok": 0, "down": 1, "nodata": 0}

    # Mêmes données, seuil d'attention par défaut (15 %) : 28 % n'est plus une alerte.
    default = client(path).get("/api/fleet/daily?days=3").json()
    assert {p["id"]: p for p in default["printers"]}["p2"]["states"][-1] == "OK"


def test_fleet_daily_validation_and_missing_db(tmp_path):
    st, path = _db(tmp_path)
    st.close()
    c = client(path)
    assert c.get("/api/fleet/daily?days=0").status_code == 422 and c.get("/api/fleet/daily?days=401").status_code == 422
    missing = TestClient(create_app(str(tmp_path / "absent.db")))
    for url in ("/api/series", "/api/availability", "/api/fleet/daily"):
        assert missing.get(url).status_code == 503


def test_new_endpoints_carry_security_headers_and_do_not_write(tmp_path):
    st, path = _db(tmp_path)
    st.close()
    before = (tmp_path / "t.db").read_bytes()
    c = client(path)
    for url in ("/api/series", "/api/availability", "/api/fleet/daily"):
        r = c.get(url)
        assert r.status_code == 200
        assert "default-src 'none'" in r.headers["content-security-policy"]
    assert (tmp_path / "t.db").read_bytes() == before         # fichier de base inchangé (SQLite peut créer -shm/-wal en lecture)
