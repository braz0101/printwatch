"""Réglages dynamiques : seuils et encres suivies, modifiés depuis le dashboard, appliqués à l'affichage et aux alertes."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from printwatch.alerts import AlertSettings, Thresholds, supply_group  # noqa: E402
from printwatch.auth import UserStore  # noqa: E402
from printwatch.collector import Collector  # noqa: E402
from printwatch.config import CollectorConfig  # noqa: E402
from printwatch.model import Printer, RawSupply  # noqa: E402
from printwatch.storage import Storage  # noqa: E402
from printwatch.web import create_app  # noqa: E402

ORIGIN = {"Origin": "http://testserver"}
ALL = ["black", "cyan", "magenta", "yellow", "box", "other"]


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "t.db")
    st = Storage(path)
    with st.conn:
        st.conn.execute("INSERT INTO printers (id,name,host,last_seen) VALUES ('p1','Compta','10.0.0.1',1)")
        for key, color, kind, pct in (("1.1", "black", "consumable", 30), ("1.2", "cyan", "consumable", 12),
                                      ("1.3", "magenta", "consumable", 4), ("1.9", None, "receptacle", 3)):
            st.conn.execute("INSERT INTO supplies (printer_id,key,description,kind,color,status,pct,alert_level,updated_at) "
                            "VALUES ('p1',?,?,?,?,'ok',?,0,1)", (key, key, kind, color, pct))
    st.close()
    return path


def alerts(c):
    p = c.get("/api/status").json()["printers"][0]
    return {s["key"]: (s["alert"], s["tracked"]) for s in p["supplies"]}, p["state"]


def test_defaults_come_from_config(db):
    c = TestClient(create_app(db, warn=20, crit=10))
    assert c.get("/api/settings").json() == {"warn": 20, "crit": 10, "inks": ALL, "choices": ALL}
    got, state = alerts(c)
    assert got["1.2"] == ("WARN", True) and got["1.3"] == ("CRIT", True) and got["1.9"] == ("CRIT", True)
    assert state == "CRIT"


def test_new_thresholds_apply_immediately_without_a_new_reading(db):
    c = TestClient(create_app(db))
    assert alerts(c)[0]["1.1"] == ("OK", True)
    r = c.put("/api/settings", json={"warn": 40, "crit": 2, "inks": ALL}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["warn"] == 40
    got, _ = alerts(c)
    assert got["1.1"] == ("WARN", True)                           # 30 % : sous le nouveau seuil d'attention
    assert got["1.3"] == ("WARN", True)                           # 4 % : plus critique avec un seuil critique à 2 %
    assert c.get("/api/status").json()["thresholds"] == {"warn": 40, "crit": 2, "inks": ALL}


def test_untracked_inks_stay_visible_without_alert(db):
    c = TestClient(create_app(db))
    c.put("/api/settings", json={"warn": 15, "crit": 5, "inks": ["black"]}, headers=ORIGIN)
    got, state = alerts(c)
    assert set(got) == {"1.1", "1.2", "1.3", "1.9"}               # rien n'est masqué
    assert got["1.3"] == ("OK", False) and got["1.9"] == ("OK", False) and got["1.1"] == ("OK", True)
    assert state == "OK"
    st = c.get("/api/status").json()
    assert st["summary"]["crit"] == 0


def test_settings_validation(db):
    c = TestClient(create_app(db))
    put = lambda body: c.put("/api/settings", json=body, headers=ORIGIN).status_code      # noqa: E731
    assert put({"warn": 5, "crit": 10, "inks": ALL}) == 422       # critique au-dessus de l'attention
    assert put({"warn": 15, "crit": 5, "inks": []}) == 422
    assert put({"warn": 15, "crit": 5, "inks": ["rouge"]}) == 422
    assert put({"warn": 150, "crit": 5, "inks": ALL}) == 422
    assert put({"warn": 15, "crit": 5, "inks": ALL, "x": 1}) == 422
    assert c.put("/api/settings", json={"warn": 15, "crit": 5, "inks": ALL}, headers={"Origin": "https://ailleurs.example"}).status_code == 403


def test_settings_need_a_session_when_auth_is_on(db, tmp_path):
    users = UserStore(tmp_path / "u.json")
    users.add("alpha@exemple.sn", "alpha@exemple.sn", policy=False, role="admin", must_change=False)
    c = TestClient(create_app(db, users=users), base_url="https://testserver")
    assert c.get("/api/settings").status_code == 401
    assert c.put("/api/settings", json={"warn": 15, "crit": 5, "inks": ALL}, headers={"Origin": "https://testserver"}).status_code == 401
    c.post("/api/auth/login", json={"email": "alpha@exemple.sn", "password": "alpha@exemple.sn"}, headers={"Origin": "https://testserver"})
    assert c.put("/api/settings", json={"warn": 25, "crit": 5, "inks": ALL}, headers={"Origin": "https://testserver"}).status_code == 200


def test_old_database_without_settings_table_uses_defaults(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    sqlite3.connect(path).executescript("CREATE TABLE x (a)")
    from printwatch.web import ReadOnlyStorage
    assert ReadOnlyStorage(str(path)).alert_settings(AlertSettings(15, 5)).warn == 15


def test_supply_groups():
    assert supply_group("consumable", "black") == "black"
    assert supply_group("receptacle", None) == "box"
    assert supply_group("consumable", None) == "other"
    assert supply_group("consumable", "red") == "other"


# --- collecteur : courriels d'alerte -----------------------------------------------------------------------------
class Client:
    def __init__(self, levels):
        self.levels = levels

    def supplies(self, host):
        return [RawSupply("1.1", 3, 5, "Black Ink", 19, 100, self.levels["black"]),      # couleur déduite de la description
                RawSupply("1.2", 3, 5, "Cyan Ink", 19, 100, self.levels["cyan"])]

    def sys_descr(self, host):
        return "EPSON"


class Notif:
    def __init__(self):
        self.sent = []

    def send(self, events):
        self.sent += [(e["type"], e["key"]) for e in events]


def test_collector_follows_settings_for_alert_emails(tmp_path):
    st = Storage(str(tmp_path / "c.db"))
    client, notif, t = Client({"black": 50, "cyan": 50}), Notif(), [1000.0]
    col = Collector(st, client, Thresholds(15, 5, 5), notif, CollectorConfig(retry_delay_s=0), lambda: t[0])
    p = Printer("p1", "Compta", "10.0.0.1")
    col.run_cycle([p])
    st.set_alert_settings(AlertSettings(15, 5, frozenset({"black"})))       # cyan n'est plus suivi
    client.levels = {"black": 3, "cyan": 45}
    t[0] += 60
    col.run_cycle([p])
    client.levels = {"black": 3, "cyan": 2}
    t[0] += 60
    col.run_cycle([p])
    assert notif.sent == [("critical", "1.1")]                             # seul le noir alerte ; cyan à 2 % ignoré
    st.set_alert_settings(AlertSettings(60, 5, frozenset({"black", "cyan"})))
    client.levels = {"black": 3, "cyan": 2}
    t[0] += 60
    col.run_cycle([p])
    assert notif.sent[-1] == ("critical", "1.2")                            # cyan de nouveau suivi : réglages relus au cycle suivant
    assert (col.th.warn, col.th.crit, col.th.hysteresis) == (60, 5, 5)      # seuils du dashboard, hystérésis de config.toml
