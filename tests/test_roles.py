"""Droits par rôle (lecture / écriture / admin) et incidents saisis sur une imprimante."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from printwatch.auth import AuthError, UserStore  # noqa: E402
from printwatch.cli import main  # noqa: E402
from printwatch.storage import Storage  # noqa: E402
from printwatch.sync import SyncController  # noqa: E402
from printwatch.web import create_app  # noqa: E402

ORIGIN = {"Origin": "https://testserver"}
ALL = ["black", "cyan", "magenta", "yellow", "box", "other"]


@pytest.fixture
def env(tmp_path):
    db = str(tmp_path / "t.db")
    st = Storage(db)
    with st.conn:
        st.conn.execute("INSERT INTO printers (id,name,host) VALUES ('p1','Compta','10.0.0.1')")
    st.close()
    users = UserStore(tmp_path / "users.json")
    for email, role in (("admin@exemple.sn", "admin"), ("ecrit@exemple.sn", "ecriture"), ("lit@exemple.sn", "lecture")):
        users.add(email, email, policy=False, role=role, must_change=False)
    app = create_app(db, users=users, manage="open", sync=SyncController(min_gap_s=0))

    def as_(email):
        c = TestClient(app, base_url="https://testserver")
        assert c.post("/api/auth/login", json={"email": email, "password": email}, headers=ORIGIN).status_code == 200
        return c
    return {"as": as_, "users": users, "db": db}


# --- droits ----------------------------------------------------------------------------------------------------------
def test_reader_can_view_but_not_write(env):
    c = env["as"]("lit@exemple.sn")
    st = c.get("/api/status").json()
    assert st["capabilities"] == {"sync": False, "manage": "off", "incidents": False, "admin": False, "role": "lecture"}
    assert c.get("/api/printers/p1/incidents").status_code == 200
    denied = [
        c.post("/api/sync", headers=ORIGIN),
        c.post("/api/printers", json={"name": "X", "host": "10.0.0.9"}, headers=ORIGIN),
        c.patch("/api/printers/p1", json={"name": "Y"}, headers=ORIGIN),
        c.delete("/api/printers/p1", headers=ORIGIN),
        c.post("/api/printers/p1/incidents", json={"category": "Autre", "description": "x"}, headers=ORIGIN),
        c.put("/api/settings", json={"warn": 20, "crit": 5, "inks": ALL}, headers=ORIGIN),
        c.get("/api/users"),
    ]
    assert [r.status_code for r in denied] == [403] * len(denied)
    assert "droits insuffisants" in denied[0].json()["detail"]


def test_reader_can_still_change_own_password(env):
    c = env["as"]("lit@exemple.sn")
    assert c.post("/api/auth/password", json={"current": "lit@exemple.sn", "new": "Nouveau-mot-2026"}, headers=ORIGIN).status_code == 200


def test_writer_syncs_edits_and_declares_but_no_admin(env):
    c = env["as"]("ecrit@exemple.sn")
    caps = c.get("/api/status").json()["capabilities"]
    assert caps == {"sync": True, "manage": "open", "incidents": True, "admin": False, "role": "ecriture"}
    assert c.post("/api/sync", headers=ORIGIN).status_code == 202
    assert c.patch("/api/printers/p1", json={"location": "Bureau 2"}, headers=ORIGIN).status_code == 200      # sans code administrateur
    assert c.post("/api/printers/p1/incidents", json={"category": "Autre", "description": "x"}, headers=ORIGIN).status_code == 201
    assert c.put("/api/settings", json={"warn": 20, "crit": 5, "inks": ALL}, headers=ORIGIN).status_code == 403
    assert c.get("/api/users").status_code == 403
    assert c.post("/api/users", json={"email": "n@exemple.sn", "password": "Provisoire-2026"}, headers=ORIGIN).status_code == 403


def test_admin_does_everything_and_sets_roles(env):
    c = env["as"]("admin@exemple.sn")
    assert c.get("/api/status").json()["capabilities"]["admin"] is True
    assert c.put("/api/settings", json={"warn": 20, "crit": 5, "inks": ALL}, headers=ORIGIN).status_code == 200
    assert {u["email"]: u["role"] for u in c.get("/api/users").json()} == {
        "admin@exemple.sn": "admin", "ecrit@exemple.sn": "ecriture", "lit@exemple.sn": "lecture"}
    r = c.post("/api/users", json={"email": "n@exemple.sn", "password": "Provisoire-2026", "role": "lecture"}, headers=ORIGIN)
    assert r.status_code == 201 and env["users"].role("n@exemple.sn") == "lecture"
    assert c.patch("/api/users/lit@exemple.sn/role", json={"role": "ecriture"}, headers=ORIGIN).json() == {"email": "lit@exemple.sn", "role": "ecriture"}
    assert c.patch("/api/users/lit@exemple.sn/role", json={"role": "chef"}, headers=ORIGIN).status_code == 422
    assert c.patch("/api/users/admin@exemple.sn/role", json={"role": "lecture"}, headers=ORIGIN).status_code == 409   # pas soi-même
    assert c.patch("/api/users/inconnu@exemple.sn/role", json={"role": "lecture"}, headers=ORIGIN).status_code == 404


def test_role_change_applies_to_open_sessions(env):
    reader = env["as"]("lit@exemple.sn")
    assert reader.post("/api/sync", headers=ORIGIN).status_code == 403
    env["users"].set_role("lit@exemple.sn", "ecriture")
    assert reader.post("/api/sync", headers=ORIGIN).status_code == 202          # sans se reconnecter
    env["users"].set_role("lit@exemple.sn", "lecture")
    assert reader.post("/api/sync", headers=ORIGIN).status_code == 403


def test_last_admin_is_protected(env):
    users = env["users"]
    with pytest.raises(AuthError):
        users.set_role("admin@exemple.sn", "ecriture")
    with pytest.raises(AuthError):
        users.remove("admin@exemple.sn")
    users.set_role("ecrit@exemple.sn", "admin")
    users.set_role("admin@exemple.sn", "ecriture")                                 # un autre administrateur existe : permis


def test_account_without_role_is_read_only(tmp_path):
    import json
    path = tmp_path / "u.json"
    UserStore(path).add("a@exemple.sn", "a@exemple.sn", policy=False)
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["users"]["a@exemple.sn"]["role"]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert UserStore(path).role("a@exemple.sn") == "lecture"


def test_cli_roles(tmp_path, monkeypatch, capsys):
    (tmp_path / "data").mkdir()
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'[snmp]\nversion = "2c"\n[collector]\ndb_path = "{(tmp_path / "data" / "pw.db").as_posix()}"\n', encoding="utf-8")
    monkeypatch.setenv("PRINTWATCH_SNMP_COMMUNITY", "public")
    monkeypatch.delenv("PRINTWATCH_USERS_FILE", raising=False)
    run = lambda *a: main(["-c", str(cfg), "users", *a])          # noqa: E731
    assert run("add", "--temp", "--role", "admin", "a@exemple.sn") == 0
    assert run("add", "--temp", "b@exemple.sn", "c@exemple.sn") == 0
    assert run("role", "lecture", "c@exemple.sn") == 0
    st = UserStore(tmp_path / "data" / "users.json")
    assert [st.role(e) for e in ("a@exemple.sn", "b@exemple.sn", "c@exemple.sn")] == ["admin", "ecriture", "lecture"]
    assert run("role", "lecture", "a@exemple.sn") == 2                 # dernier administrateur
    capsys.readouterr()
    assert run("list") == 0 and "lecture" in capsys.readouterr().out


# --- incidents -------------------------------------------------------------------------------------------------------
def test_declare_list_and_resolve_incident(env):
    c = env["as"]("ecrit@exemple.sn")
    r = c.post("/api/printers/p1/incidents", json={"category": "Bourrage papier", "description": "  Bac 2 bloqué\nà revoir  "}, headers=ORIGIN)
    assert r.status_code == 201
    inc = r.json()
    assert (inc["category"], inc["description"], inc["opened_by"], inc["resolved_at"]) == ("Bourrage papier", "Bac 2 bloqué\nà revoir", "ecrit@exemple.sn", None)
    p = c.get("/api/status").json()["printers"][0]
    assert [i["id"] for i in p["incidents"]] == [inc["id"]]                       # ouvert : visible dans l'état du parc

    admin = env["as"]("admin@exemple.sn")
    r = admin.post(f"/api/incidents/{inc['id']}/resolve", json={"resolution": "Papier retiré"}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["resolved_by"] == "admin@exemple.sn" and r.json()["resolution"] == "Papier retiré"
    assert admin.post(f"/api/incidents/{inc['id']}/resolve", json={}, headers=ORIGIN).status_code == 409
    assert c.get("/api/status").json()["printers"][0]["incidents"] == []
    hist = env["as"]("lit@exemple.sn").get("/api/printers/p1/incidents").json()     # la lecture voit l'historique
    assert len(hist) == 1 and hist[0]["resolution"] == "Papier retiré"


def test_history_lists_open_first_then_newest(env):
    st = Storage(env["db"])
    a = st.add_incident("p1", "Autre", "ancien", 100.0, "x")
    st.add_incident("p1", "Autre", "récent", 300.0, "x")
    st.add_incident("p1", "Autre", "ouvert", 200.0, "x")
    st.resolve_incident(a, 150.0, "x", "")
    st.resolve_incident(a + 1, 350.0, "x", "")
    assert [i["description"] for i in st.incidents("p1")] == ["ouvert", "récent", "ancien"]
    st.close()


def test_incident_validation(env):
    c = env["as"]("ecrit@exemple.sn")
    post = lambda body, pid="p1": c.post(f"/api/printers/{pid}/incidents", json=body, headers=ORIGIN).status_code   # noqa: E731
    assert post({"category": "Inventé", "description": "x"}) == 422
    assert post({"category": "Autre", "description": "   "}) == 422
    assert post({"category": "Autre", "description": "x" * 1001}) == 422
    assert post({"category": "Autre", "description": "a\x07b"}) == 422
    assert post({"category": "Autre", "description": "x"}, pid="inconnue") == 404
    assert c.post("/api/incidents/999/resolve", json={}, headers=ORIGIN).status_code == 404
    assert c.post("/api/printers/p1/incidents", json={"category": "Autre", "description": "x"},
                  headers={"Origin": "https://ailleurs.example"}).status_code == 403
    assert c.get("/api/incidents/categories").json()[0] == "Bourrage papier"


def test_incidents_need_a_session(env):
    anon = TestClient(create_app(env["db"], users=env["users"]), base_url="https://testserver")
    assert anon.get("/api/printers/p1/incidents").status_code == 401
    assert anon.post("/api/printers/p1/incidents", json={"category": "Autre", "description": "x"}, headers=ORIGIN).status_code == 401
