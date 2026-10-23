"""Connexion au tableau de bord : refus sans session, cookie signé, blocage après échecs, gestion des comptes."""
import base64
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from printwatch.auth import REMEMBER_TTL, AuthError, Throttle, UserStore, hash_password, verify_password  # noqa: E402
from printwatch.cli import main  # noqa: E402
from printwatch.storage import Storage  # noqa: E402
from printwatch.web import SESSION_COOKIE, create_app  # noqa: E402

ACCOUNTS = ["alpha@exemple.sn", "bravo@exemple.sn", "CHARLIE@exemple.sn", "Delta.Echo@exemple.sn"]
ADMINS = {"alpha@exemple.sn", "delta.echo@exemple.sn"}          # deux administrateurs, deux comptes en écriture
ORIGIN = {"Origin": "https://testserver"}


class Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def env(tmp_path):
    db = str(tmp_path / "t.db")
    Storage(db).close()
    clock = Clock()
    users = UserStore(tmp_path / "users.json", clock=clock)
    for e in ACCOUNTS:
        users.add(e, e.lower(), must_change=False, policy=False,       # comptes ordinaires (le provisoire est testé à part)
                  role="admin" if e.lower() in ADMINS else "ecriture")
    app = create_app(db, users=users, clock=clock, throttle=Throttle(clock=clock))
    return {"users": users, "clock": clock, "client": TestClient(app, base_url="https://testserver"), "path": tmp_path / "users.json"}


def login(c, email="alpha@exemple.sn", password="alpha@exemple.sn", remember=False):
    return c.post("/api/auth/login", json={"email": email, "password": password, "remember": remember}, headers=ORIGIN)


# --- refus d'accès ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/api/status", "/api/events", "/api/series", "/api/availability", "/api/fleet/daily",
                                  "/api/history/p1/1.1", "/api/users", "/api/auth/me"])
def test_api_refused_without_session(env, path):
    r = env["client"].get(path)
    assert r.status_code == 401 and r.json()["detail"] == "connexion requise"


def test_writes_refused_without_session(env):
    c = env["client"]
    assert c.post("/api/sync", headers=ORIGIN).status_code == 401
    assert c.post("/api/printers", json={"name": "x", "host": "10.0.0.1"}, headers=ORIGIN).status_code == 401
    assert c.post("/api/users", json={"email": "x@exemple.sn", "password": "abcdefghijk"}, headers=ORIGIN).status_code == 401


def test_login_page_and_health_stay_public(env):
    c = env["client"]
    assert c.get("/").status_code == 200                           # coquille de l'application, sans aucune donnée
    assert c.get("/api/health").json() == {"ok": True}


def test_forged_or_tampered_cookie_refused(env):
    c = env["client"]
    login(c)
    token = c.cookies.get(SESSION_COOKIE)
    with_cookie = lambda t: TestClient(c.app, base_url="https://testserver").get(   # noqa: E731
        "/api/status", headers={"Cookie": f"{SESSION_COOKIE}={t}"}).status_code
    assert with_cookie(token) == 200
    payload, sig = token.split(".")
    assert with_cookie(payload + "." + ("A" if sig[0] != "A" else "B") + sig[1:]) == 401
    admin = json.dumps({"u": "alpha@exemple.sn", "sv": 1, "r": 1, "exp": 9e12}).encode()
    assert with_cookie(base64.urlsafe_b64encode(admin).decode().rstrip("=") + "." + sig) == 401     # charge modifiée
    other = UserStore(env["path"].with_name("autre.json"))
    other.add("alpha@exemple.sn", "un-autre-secret", policy=False)
    forged, _ = other.issue("alpha@exemple.sn", False)                  # signé avec une autre clé
    assert with_cookie(forged) == 401


# --- connexion -------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("email", ACCOUNTS)
def test_four_accounts_log_in_with_their_address_case_insensitive(env, email):
    c = env["client"]
    r = login(c, email.upper(), email.lower())
    assert r.status_code == 200
    assert r.json() == {"auth": True, "email": email.lower(), "role": "admin" if email.lower() in ADMINS else "ecriture", "must_change": False}
    assert c.get("/api/status").status_code == 200
    assert c.get("/api/auth/me").json()["email"] == email.lower()


def test_cookie_flags(env):
    r = login(env["client"])
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert "max-age" not in cookie                                 # session ordinaire : oubliée à la fermeture du navigateur
    remembered = login(env["client"], remember=True).headers["set-cookie"].lower()
    assert f"max-age={REMEMBER_TTL}" in remembered


def test_wrong_password_or_unknown_account(env):
    c = env["client"]
    assert login(c, password="mauvais").status_code == 401
    r = login(c, email="inconnu@exemple.sn", password="inconnu@exemple.sn")
    assert r.status_code == 401 and r.json()["detail"] == "adresse ou mot de passe incorrect"   # même message : pas d'énumération
    assert SESSION_COOKIE not in c.cookies


def test_login_from_other_origin_refused(env):
    r = env["client"].post("/api/auth/login", json={"email": "alpha@exemple.sn", "password": "alpha@exemple.sn"},
                           headers={"Origin": "https://malveillant.example"})
    assert r.status_code == 403


def test_logout_clears_cookie(env):
    c = env["client"]
    login(c)
    assert c.post("/api/auth/logout", headers=ORIGIN).status_code == 200
    assert c.get("/api/status").status_code == 401


def test_session_expires(env):
    c = env["client"]
    login(c)
    env["clock"].t += 13 * 3600
    assert c.get("/api/status").status_code == 401


def test_remember_me_session_is_renewed_and_lasts(env):
    """Écran mural : la session « rester connecté » est prolongée tant que la page interroge l'API."""
    c = env["client"]
    login(c, remember=True)
    first = c.cookies.get(SESSION_COOKIE)
    env["clock"].t += 13 * 3600
    r = c.get("/api/status")
    assert r.status_code == 200 and "set-cookie" not in r.headers                      # encore loin de la mi-vie
    env["clock"].t += REMEMBER_TTL * 0.6
    r = c.get("/api/status")
    assert r.status_code == 200 and SESSION_COOKIE in r.headers.get("set-cookie", "")
    assert c.cookies.get(SESSION_COOKIE) != first
    env["clock"].t += REMEMBER_TTL * 0.6                                                # au-delà de l'échéance du premier cookie
    assert c.get("/api/status").status_code == 200


# --- blocage ---------------------------------------------------------------------------------------------------------
def test_lockout_after_five_failures_then_released(env):
    c = env["client"]
    for _ in range(5):
        assert login(c, password="mauvais").status_code == 401
    r = login(c)                                                   # même le bon mot de passe est refusé pendant le blocage
    assert r.status_code == 429 and "réessayez dans 15 min" in r.json()["detail"] and int(r.headers["retry-after"]) > 800
    env["clock"].t += 901
    assert login(c).status_code == 200


def test_lockout_is_per_account_and_per_workstation(env):
    c = env["client"]
    for i in range(5):                                             # cinq comptes inexistants depuis le même poste
        login(c, email=f"x{i}@exemple.sn", password="nope")
    assert login(c, email="bravo@exemple.sn", password="bravo@exemple.sn").status_code == 429


def test_lockout_ignores_email_case(env):
    c = env["client"]
    for e in ("CHARLIE@exemple.sn", "charlie@EXEMPLE.sn", "Charlie@exemple.sn", "charlie@exemple.sn", "CHARLIE@EXEMPLE.SN"):
        env["client"].headers["x-forwarded-for"] = f"10.0.0.{len(e)}"                  # postes variés : seul le compte compte
        login(c, email=e, password="mauvais")
    assert login(c, email="charlie@exemple.sn", password="charlie@exemple.sn").status_code == 429


def test_success_resets_failure_count(env):
    c = env["client"]
    for _ in range(4):
        login(c, password="mauvais")
    assert login(c).status_code == 200
    for _ in range(4):
        login(c, password="mauvais")
    assert login(c).status_code == 200


# --- gestion des comptes ---------------------------------------------------------------------------------------------
def test_change_own_password(env):
    c = env["client"]
    login(c)
    r = c.post("/api/auth/password", json={"current": "alpha@exemple.sn", "new": "Nouveau-mot-2026"}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["must_change"] is False
    assert c.get("/api/status").status_code == 200                 # la session courante est renouvelée
    other = TestClient(c.app, base_url="https://testserver")
    assert login(other).status_code == 401                         # l'ancien mot de passe ne marche plus
    assert login(other, password="Nouveau-mot-2026").status_code == 200


def test_change_password_checks(env):
    c = env["client"]
    login(c)
    post = lambda cur, new: c.post("/api/auth/password", json={"current": cur, "new": new}, headers=ORIGIN)   # noqa: E731
    assert post("faux", "Nouveau-mot-2026").status_code == 403
    assert post("alpha@exemple.sn", "court").status_code == 422
    assert post("alpha@exemple.sn", "ALPHA@exemple.sn").status_code == 422   # l'adresse n'est pas un mot de passe
    assert post("alpha@exemple.sn", "alpha@exemple.sn").status_code == 422


def test_password_change_closes_other_sessions(env):
    c, other = env["client"], TestClient(env["client"].app, base_url="https://testserver")
    login(c)
    login(other)
    c.post("/api/auth/password", json={"current": "alpha@exemple.sn", "new": "Nouveau-mot-2026"}, headers=ORIGIN)
    assert other.get("/api/status").status_code == 401


def test_list_add_reset_remove_accounts(env):
    c = env["client"]
    login(c)
    assert [u["email"] for u in c.get("/api/users").json()] == sorted(e.lower() for e in ACCOUNTS)
    assert all("hash" not in u for u in c.get("/api/users").json())

    r = c.post("/api/users", json={"email": " Nouveau.Agent@EXEMPLE.sn ", "password": "Provisoire-2026"}, headers=ORIGIN)
    assert r.status_code == 201 and r.json() == {"email": "nouveau.agent@exemple.sn"}
    assert c.post("/api/users", json={"email": "nouveau.agent@exemple.sn", "password": "Provisoire-2026"}, headers=ORIGIN).status_code == 409
    assert c.post("/api/users", json={"email": "pas-une-adresse", "password": "Provisoire-2026"}, headers=ORIGIN).status_code == 422
    newbie = TestClient(c.app, base_url="https://testserver")
    assert login(newbie, "nouveau.agent@exemple.sn", "Provisoire-2026").json()["must_change"] is True

    r = c.post("/api/users/nouveau.agent@exemple.sn/password", json={"password": "Autre-provisoire-1"}, headers=ORIGIN)
    assert r.status_code == 200
    assert newbie.get("/api/status").status_code == 401            # ses sessions ouvertes sont fermées
    assert login(newbie, "nouveau.agent@exemple.sn", "Autre-provisoire-1").status_code == 200

    assert c.delete("/api/users/NOUVEAU.AGENT@exemple.sn", headers=ORIGIN).status_code == 200
    assert newbie.get("/api/status").status_code == 401            # compte retiré : accès coupé aussitôt
    assert c.delete("/api/users/nouveau.agent@exemple.sn", headers=ORIGIN).status_code == 404


def test_cannot_remove_or_reset_self(env):
    c = env["client"]
    login(c)
    assert c.delete("/api/users/ALPHA@exemple.sn", headers=ORIGIN).status_code == 409
    assert c.post("/api/users/alpha@exemple.sn/password", json={"password": "Provisoire-2026"}, headers=ORIGIN).status_code == 409


def test_account_writes_refused_from_other_origin(env):
    c = env["client"]
    login(c)
    evil = {"Origin": "https://malveillant.example"}
    assert c.post("/api/users", json={"email": "x@exemple.sn", "password": "Provisoire-2026"}, headers=evil).status_code == 403
    assert c.delete("/api/users/bravo@exemple.sn", headers=evil).status_code == 403
    assert c.post("/api/auth/password", json={"current": "alpha@exemple.sn", "new": "Nouveau-mot-2026"}, headers=evil).status_code == 403


# --- stockage --------------------------------------------------------------------------------------------------------
def test_passwords_stored_hashed(env):
    raw = env["path"].read_text(encoding="utf-8")
    for email, u in json.loads(raw)["users"].items():
        assert u["hash"].startswith("scrypt$")
        assert raw.count(email) == 1                               # seule occurrence : la clé du compte, jamais le mot de passe en clair


def test_hash_is_salted_and_verifies():
    a, b = hash_password("secret-123456"), hash_password("secret-123456")
    assert a != b and verify_password("secret-123456", a) and not verify_password("autre", a)
    assert not verify_password("x", "n'importe quoi")


def test_store_picks_up_external_changes(tmp_path):
    """Le service relit le fichier : un compte créé en ligne de commande est utilisable sans redémarrage."""
    path = tmp_path / "users.json"
    served = UserStore(path)
    assert served.list() == []
    UserStore(path).add("alpha@exemple.sn", "alpha@exemple.sn", policy=False)
    assert served.authenticate("ALPHA@exemple.sn", "alpha@exemple.sn") == "alpha@exemple.sn"


def test_store_validation(tmp_path):
    st = UserStore(tmp_path / "u.json")
    with pytest.raises(AuthError):
        st.add("pas une adresse", "Provisoire-2026")
    with pytest.raises(AuthError):
        st.add("a@exemple.sn", "court")


def test_cli_users_add_temp_list_remove(tmp_path, capsys, monkeypatch):
    """Par défaut, users.json est rangé à côté de la base (dossier data/, non versionné)."""
    (tmp_path / "data").mkdir()
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'[snmp]\nversion = "2c"\n[collector]\ndb_path = "{(tmp_path / "data" / "pw.db").as_posix()}"\n', encoding="utf-8")
    monkeypatch.setenv("PRINTWATCH_SNMP_COMMUNITY", "public")
    monkeypatch.delenv("PRINTWATCH_USERS_FILE", raising=False)
    run = lambda *a: main(["-c", str(cfg), "users", *a])          # noqa: E731
    assert run("add", "--temp", *ACCOUNTS) == 0
    out = capsys.readouterr().out
    st = UserStore(tmp_path / "data" / "users.json")
    # Le provisoire est ALÉATOIRE (affiché une fois) : l'adresse, connue de tous, n'ouvre jamais le compte.
    assert st.authenticate("delta.echo@exemple.sn", "delta.echo@exemple.sn") is None
    line = next(ln for ln in out.splitlines() if ln.startswith("créé : delta.echo@exemple.sn"))
    temp = line.split("mot de passe provisoire : ")[1].split()[0]
    assert len(temp) == 19 and st.authenticate("delta.echo@exemple.sn", temp) == "delta.echo@exemple.sn"
    assert len({ln.split("provisoire : ")[1].split()[0] for ln in out.splitlines() if "provisoire : " in ln}) == 4
    assert st.list()[0]["must_change"] is True
    assert main(["-c", str(cfg), "audit", "--action", "user_add"]) == 0          # les commandes laissent une trace
    assert capsys.readouterr().out.count("user_add") == 4
    assert run("list") == 0
    assert "charlie@exemple.sn" in capsys.readouterr().out
    assert run("add", "--temp", "alpha@exemple.sn") == 2                # déjà présent
    assert run("remove", "alpha@exemple.sn") == 0
    assert st.get("alpha@exemple.sn") is None


def test_no_auth_mode_is_explicit(tmp_path):
    """Sans UserStore (démo) : l'application le dit, et la gestion des comptes n'existe pas."""
    db = str(tmp_path / "t.db")
    Storage(db).close()
    c = TestClient(create_app(db))
    assert c.get("/api/auth/me").json() == {"auth": False, "email": None, "role": None, "must_change": False}
    assert c.get("/api/users").status_code == 404
