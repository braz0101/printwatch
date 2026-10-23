"""Durcissement (version 0.13) : chaque test répond à un défaut constaté sur la version précédente.

- mot de passe provisoire : il ouvrait toutes les données ; il ne sert plus qu'à être remplacé ;
- déconnexion : un cookie copié restait valable ; il est désormais révoqué ;
- en-têtes de sécurité absents des refus 401 / 400 ;
- adresses IP visibles du rôle « lecture » ;
- carnet des techniciens sans validation, nom inséré tel quel dans un suivi GLPI ;
- `limite` des tickets non bornée ;
- détection « déposée depuis l'intranet » aveugle aux accents et au HTML ;
- aucun journal des actions d'administration.

Données fictives (adresses RFC 5737, domaine exemple.sn).
"""
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from printwatch.auth import UserStore, temporary_password  # noqa: E402
from printwatch.config import ConfigError, GlpiConfig, load_config  # noqa: E402
from printwatch.glpi import Glpi, a_plat  # noqa: E402
from printwatch.model import Printer  # noqa: E402
from printwatch.storage import Storage  # noqa: E402
from printwatch.web import SECURITY_HEADERS, SESSION_COOKIE, create_app  # noqa: E402

ORIGIN = {"Origin": "https://testserver"}
PW = {"admin@exemple.sn": "Phrase-admin-2026", "it@exemple.sn": "Phrase-it-2026", "lit@exemple.sn": "Phrase-lit-2026"}


class FauxGlpi:
    def __init__(self):
        self.suivis, self.limites = [], []

    def est_actif(self):
        return True

    def lister_tickets(self, limite=60, ouverts_seulement=True):
        self.limites.append(limite)
        return [{"id": 7, "objet": "Écran noir", "date": "2026-10-20", "statut": 1, "statut_nom": "Nouveau",
                 "priorite": 3, "categorie": None, "depuis_intranet": False}]

    def attributaires(self, numeros):
        return {n: [] for n in numeros}

    def utilisateur_par_email(self, email):
        return None

    def attribuer(self, numero, uid):
        pass

    def ajouter_suivi(self, numero, texte, prive=False):
        self.suivis.append((numero, texte))
        return True


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    st = Storage(db)
    st.upsert_printer(Printer("imp-1", "Imprimante A", "192.0.2.11", "EPSON", "WF", "Atelier", "Utilisateur A"))
    st.close()
    users = UserStore(tmp_path / "users.json")
    for email, role in (("admin@exemple.sn", "admin"), ("it@exemple.sn", "ecriture"), ("lit@exemple.sn", "lecture")):
        users.add(email, PW[email], must_change=False, role=role)
    faux = FauxGlpi()
    import printwatch.glpi as module_glpi
    monkeypatch.setattr(module_glpi, "Glpi", lambda cfg: faux)
    app = create_app(db, users=users, manage="open", allowed_hosts=["testserver"],
                     glpi=GlpiConfig(url="https://glpi.exemple.sn/api.php", client_id="c", username="u"))

    def as_(email, password=None):
        c = TestClient(app, base_url="https://testserver")
        r = c.post("/api/auth/login", json={"email": email, "password": password or PW[email]}, headers=ORIGIN)
        assert r.status_code == 200, r.text
        return c

    return {"app": app, "as": as_, "users": users, "db": db, "glpi": faux}


def audit(db, **kw):
    st = Storage(db)
    try:
        return st.recent_audit(500, **kw)
    finally:
        st.close()


# --- mot de passe provisoire ----------------------------------------------------------------------------------------------
def test_temporary_password_is_random_and_readable():
    a, b = temporary_password(), temporary_password()
    assert a != b and len(a) == 19 and a.count("-") == 3
    assert not set(a) & set("0O1lI")


def test_a_provisional_account_can_only_change_its_password(env):
    admin = env["as"]("admin@exemple.sn")
    assert admin.post("/api/users", json={"email": "nouveau@exemple.sn", "password": "Provisoire-2026", "role": "ecriture"},
                      headers=ORIGIN).status_code == 201
    newbie = env["as"]("nouveau@exemple.sn", "Provisoire-2026")
    assert newbie.get("/api/auth/me").json()["must_change"] is True
    for path in ("/api/status", "/api/events", "/api/tickets", "/api/printers/imp-1/incidents"):
        r = newbie.get(path)
        assert r.status_code == 403 and r.json()["code"] == "must_change", path
    assert newbie.post("/api/sync", headers=ORIGIN).status_code == 403
    r = newbie.post("/api/auth/password", json={"current": "Provisoire-2026", "new": "Ma-phrase-perso-26"}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["must_change"] is False
    assert newbie.get("/api/status").status_code == 200                       # débloqué une fois le mot de passe remplacé


# --- déconnexion ------------------------------------------------------------------------------------------------------------
def test_logout_revokes_the_token_even_if_copied_but_not_other_sessions(env):
    c = env["as"]("it@exemple.sn")
    other = env["as"]("it@exemple.sn")                                         # écran mural, autre poste
    token = c.cookies.get(SESSION_COOKIE)
    thief = TestClient(env["app"], base_url="https://testserver")
    thief.cookies.set(SESSION_COOKIE, token)
    assert thief.get("/api/status").status_code == 200
    assert c.post("/api/auth/logout", headers=ORIGIN).status_code == 200
    assert thief.get("/api/status").status_code == 401                          # le jeton copié ne vaut plus rien
    assert other.get("/api/status").status_code == 200                          # l'autre session du compte reste ouverte
    assert "revoked" in json.loads((env["users"].path).read_text(encoding="utf-8"))
    assert token not in env["users"].path.read_text(encoding="utf-8")            # seule une empreinte est conservée


# --- en-têtes et données exposées -------------------------------------------------------------------------------------------
def test_security_headers_on_refusals_too(env):
    c = TestClient(env["app"], base_url="https://testserver")
    r = c.get("/api/status")
    assert r.status_code == 401 and r.headers["content-security-policy"] == SECURITY_HEADERS["Content-Security-Policy"]
    r = TestClient(env["app"], base_url="https://ailleurs.exemple").get("/")
    assert r.status_code == 400 and r.headers["x-frame-options"] == "DENY"


def test_read_only_role_does_not_see_ip_addresses(env):
    assert all("host" not in p for p in env["as"]("lit@exemple.sn").get("/api/status").json()["printers"])
    assert all(p["host"] == "192.0.2.11" for p in env["as"]("it@exemple.sn").get("/api/status").json()["printers"])


# --- carnet des techniciens et tickets ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("body", [
    {"name": "<b>Gras</b>", "glpi_email": ""},
    {"name": "Nom\u0007bip", "glpi_email": ""},
    {"name": "Technicien Deux", "glpi_email": "pas-une-adresse"},
    {"name": "Technicien Deux", "glpi_email": "", "role": "admin"},
])
def test_technician_entries_are_validated(env, body):
    assert env["as"]("admin@exemple.sn").post("/api/technicians", json=body, headers=ORIGIN).status_code == 422


def test_technician_email_is_normalized_and_name_escaped_in_glpi_followup(env):
    admin = env["as"]("admin@exemple.sn")
    r = admin.post("/api/technicians", json={"name": "Agent & Fils", "glpi_email": " Tech.Deux@EXEMPLE.sn "}, headers=ORIGIN)
    assert r.status_code == 201 and r.json()["glpi_email"] == "tech.deux@exemple.sn"
    r = admin.post("/api/technicians", json={"name": "O'Brien & Cie", "glpi_email": ""}, headers=ORIGIN)
    tid = r.json()["id"]
    assert env["as"]("it@exemple.sn").post("/api/tickets/7/confier", json={"technician_id": tid}, headers=ORIGIN).status_code == 200
    assert env["glpi"].suivis == [(7, "Ticket confié à O&#x27;Brien &amp; Cie depuis PrintWatch.")]


def test_ticket_limit_is_bounded(env):
    it = env["as"]("it@exemple.sn")
    assert it.get("/api/tickets?limite=100000").status_code == 422
    assert it.get("/api/tickets?limite=0").status_code == 422
    assert it.get("/api/tickets?limite=200").status_code == 200
    assert env["glpi"].limites == [200]


def test_glpi_client_also_clamps_the_limit():
    class Prise(Glpi):
        def est_actif(self):
            return True

        def _lire(self, chemin, **kw):
            self.chemin = chemin
            return []
    g = Prise(GlpiConfig(url="https://glpi.exemple.sn/api.php", client_id="c", username="u"), secrets={})
    g.lister_tickets(limite=5000)
    assert "limit=200&" in g.chemin


# --- détection des demandes de l'intranet -------------------------------------------------------------------------------------
@pytest.mark.parametrize("contenu,attendu", [
    ("Demande déposée depuis l'intranet.", True),
    ("<p>Demande d&eacute;pos&eacute;e depuis l&#039;intranet.</p>", True),
    ("DEMANDE DÉPOSÉE DEPUIS L’INTRANET", True),
    ("Demande deposee depuis l'intranet", True),
    ("Courriel transféré qui parle de l'intranet", False),
    ("", False),
])
def test_intranet_detection_ignores_accents_case_and_html(contenu, attendu):
    g = Glpi(GlpiConfig(url="https://glpi.exemple.sn/api.php", client_id="c", username="u"), secrets={})
    assert g.vient_de_l_intranet(contenu) is attendu


def test_intranet_markers_are_configurable(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[snmp]\nversion = "2c"\n[glpi]\nurl = "https://glpi.exemple.sn/api.php"\nclient_id = "c"\nusername = "u"\n'
                   'marqueurs_intranet = ["envoye depuis", "portail interne"]\n', encoding="utf-8")
    g = Glpi(load_config(cfg).glpi, secrets={})
    assert g.vient_de_l_intranet("Message envoyé depuis le Portail interne")
    assert not g.vient_de_l_intranet("Demande déposée depuis l'intranet")
    cfg.write_text('[snmp]\nversion = "2c"\n[glpi]\nurl = "https://glpi.exemple.sn/api.php"\nclient_id = "c"\nusername = "u"\n'
                   'marqueurs_intranet = []\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(cfg)
    assert a_plat("Déposée") == "deposee"


# --- journal d'audit --------------------------------------------------------------------------------------------------------
def test_every_sensitive_action_is_audited_without_secrets(env):
    c = TestClient(env["app"], base_url="https://testserver")
    c.post("/api/auth/login", json={"email": "admin@exemple.sn", "password": "mauvais-mot"}, headers=ORIGIN)
    c.post("/api/auth/login", json={"email": "Phrase-secrete-tapee-ici", "password": "x"}, headers=ORIGIN)
    admin = env["as"]("admin@exemple.sn")
    admin.post("/api/users", json={"email": "b@exemple.sn", "password": "Provisoire-2026", "role": "lecture"}, headers=ORIGIN)
    admin.patch("/api/users/b@exemple.sn/role", json={"role": "ecriture"}, headers=ORIGIN)
    admin.post("/api/users/b@exemple.sn/password", json={"password": "Autre-provisoire-1"}, headers=ORIGIN)
    admin.delete("/api/users/b@exemple.sn", headers=ORIGIN)
    admin.put("/api/settings", json={"warn": 20, "crit": 5, "inks": ["black", "box"]}, headers=ORIGIN)
    admin.patch("/api/printers/imp-1", json={"user": "Utilisateur B"}, headers=ORIGIN)
    r = admin.post("/api/printers/imp-1/incidents", json={"category": "Bourrage papier", "description": "bac 2"}, headers=ORIGIN)
    admin.post(f"/api/incidents/{r.json()['id']}/resolve", json={"resolution": "papier retiré"}, headers=ORIGIN)
    tid = admin.post("/api/technicians", json={"name": "Technicien Un"}, headers=ORIGIN).json()["id"]
    admin.post("/api/tickets/7/confier", json={"technician_id": tid}, headers=ORIGIN)
    admin.post("/api/auth/password", json={"current": PW["admin@exemple.sn"], "new": "Nouvelle-phrase-26"}, headers=ORIGIN)
    admin.delete("/api/printers/imp-1", headers=ORIGIN)
    admin.post("/api/auth/logout", headers=ORIGIN)

    rows = audit(env["db"])
    actions = [r["action"] for r in rows]
    for a in ("login_failed", "login_ok", "user_add", "user_role", "user_reset", "user_remove", "settings", "printer_edit",
              "incident_add", "incident_resolve", "technician_add", "ticket_assign", "password_change", "printer_remove", "logout"):
        assert a in actions, a
    by = {r["action"]: r for r in rows}
    assert by["user_role"]["target"] == "b@exemple.sn : lecture → ecriture" and by["user_role"]["user"] == "admin@exemple.sn"
    assert by["printer_edit"]["target"] == "imp-1 (Imprimante A) — user : « Utilisateur A » → « Utilisateur B »"
    assert {r["user"] for r in rows if r["action"] == "login_failed"} == {"admin@exemple.sn", "(inconnu)"}
    dump = json.dumps(rows) + open(env["db"], "rb").read().decode("latin-1")
    for secret in ("mauvais-mot", "Phrase-secrete-tapee-ici", "Provisoire-2026", "Autre-provisoire-1", "Nouvelle-phrase-26", PW["admin@exemple.sn"]):
        assert secret not in dump, secret


def test_audit_is_admin_only_filterable_and_csv_safe(env):
    admin = env["as"]("admin@exemple.sn")
    assert env["as"]("it@exemple.sn").get("/api/audit").status_code == 403
    assert env["as"]("lit@exemple.sn").get("/api/audit.csv").status_code == 403
    admin.post("/api/technicians", json={"name": "=HYPERLINK(1)", "glpi_email": ""}, headers=ORIGIN)
    page = admin.get("/api/audit?limit=1").json()
    assert len(page["entries"]) == 1 and page["more"] is True
    older = admin.get(f"/api/audit?before={page['entries'][0]['id']}").json()["entries"]
    assert all(e["id"] < page["entries"][0]["id"] for e in older)
    only = admin.get("/api/audit?action=login").json()["entries"]
    assert only and all(e["action"].startswith("login") for e in only)
    assert all(e["user"] == "it@exemple.sn" for e in admin.get("/api/audit?user=IT@exemple.sn").json()["entries"])
    csv_text = admin.get("/api/audit.csv").content.decode("utf-8-sig")
    assert csv_text.startswith("date;compte;action;détail;poste")
    assert "technician_add;'=HYPERLINK(1) (hors GLPI);" in csv_text           # formule neutralisée pour Excel
    assert "technician_add;=HYPERLINK" not in csv_text


def test_cli_account_commands_are_audited(tmp_path, capsys):
    from printwatch.cli import main
    (tmp_path / "data").mkdir()
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'[snmp]\nversion = "2c"\n[collector]\ndb_path = "{(tmp_path / "data" / "pw.db").as_posix()}"\n', encoding="utf-8")
    assert main(["-c", str(cfg), "users", "add", "--temp", "--role", "admin", "chef@exemple.sn"]) == 0
    assert main(["-c", str(cfg), "users", "role", "ecriture", "chef@exemple.sn"]) == 2              # dernier administrateur : refusé
    assert main(["-c", str(cfg), "users", "add", "--temp", "b@exemple.sn"]) == 0
    assert main(["-c", str(cfg), "users", "remove", "b@exemple.sn"]) == 0
    capsys.readouterr()
    rows = audit(str(tmp_path / "data" / "pw.db"))
    assert [r["action"] for r in rows][:3] == ["user_remove", "user_add", "user_add"]
    assert all(r["user"].startswith("cli") and r["source"] == "cli" for r in rows)
