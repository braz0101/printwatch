"""
Répartition des tickets d'assistance (GLPI).

POURQUOI CE MODULE EXISTE. Les tickets se répartissaient depuis l'intranet, qui est
l'outil de tout le personnel ; depuis le 21/10/2026 ils se gèrent dans PrintWatch, où
seules les personnes du service informatique ont un compte. Déposer une demande reste sur
l'intranet — c'est là que sont les agents.

CE QUE CES TESTS GARDENT, et chacun répond à une façon précise de se tromper :

  - **LA COLONNE « confié à » A DEUX SOURCES**, et il faut les deux. GLPI fait foi quand
    l'attributaire y a un compte ; sinon, seule la table locale sait à qui le ticket a été
    confié. Un écran qui ne lirait que GLPI afficherait « libre » sur un ticket pourtant
    attribué.
  - **UN TECHNICIEN PEUT NE PAS AVOIR DE COMPTE GLPI.** Le cas s'est présenté avec un
    stagiaire de passage, et il ne se contourne pas : l'API exige un identifiant
    d'utilisateur pour désigner un acteur. L'attribution devient alors un suivi dans la
    chronologie, et l'attributaire est marqué « hors GLPI ».
  - **GLPI D'ABORD, LA TABLE ENSUITE.** Si GLPI refuse, rien n'est enregistré : un ticket
    qu'on croit confié et que personne ne traite est pire qu'un refus affiché.
  - **LA LECTURE EXIGE « écriture ».** La liste porte les intitulés des demandes de tout
    le personnel ; le rôle « lecture » existe pour regarder des niveaux d'encre.
  - **SANS CONFIGURATION, LA ROUTE REFUSE** au lieu de rendre une liste vide : un écran
    vide en silence fait croire qu'il n'y a rien à faire.
"""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from printwatch.auth import UserStore  # noqa: E402
from printwatch.config import GlpiConfig  # noqa: E402
from printwatch.glpi import ErreurGlpi  # noqa: E402
from printwatch.storage import Storage  # noqa: E402
from printwatch.web import create_app  # noqa: E402

ORIGIN = {"Origin": "https://testserver"}

TICKETS = [
    {"id": 5532, "objet": "Accès à l’application comptable", "date": "2026-10-20", "statut": 1,
     "statut_nom": "Nouveau", "priorite": 3, "categorie": "Paramétrage",
     "depuis_intranet": True},
    {"id": 5512, "objet": "RE: Compte de messagerie", "date": "2026-10-13", "statut": 1,
     "statut_nom": "Nouveau", "priorite": 3, "categorie": None,
     "depuis_intranet": False},
]


class FauxGlpi:
    """Le vrai client, dont seul le réseau est simulé.

    On ne remplace pas `lister_tickets` ni `confier` : ce sont les méthodes qui portent
    les règles. Un test qui les remplacerait ne vérifierait que son propre faux.
    """

    def __init__(self, *, assignes=None, compte=None, echoue_attribution=False):
        self.assignes = assignes or {}
        self.compte = compte
        self.echoue_attribution = echoue_attribution
        self.suivis: list[tuple[int, str]] = []
        self.attributions: list[tuple[int, int]] = []

    def est_actif(self):
        return True

    def lister_tickets(self, limite=60, ouverts_seulement=True):
        return [dict(t) for t in TICKETS]

    def attributaires(self, numeros):
        return {n: self.assignes.get(n, []) for n in numeros}

    def utilisateur_par_email(self, email):
        return self.compte

    def attribuer(self, numero, glpi_user_id):
        if self.echoue_attribution:
            raise ErreurGlpi("GLPI a répondu 403")
        self.attributions.append((numero, glpi_user_id))

    def ajouter_suivi(self, numero, texte, prive=False):
        self.suivis.append((numero, texte))
        return True


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    Storage(db).close()

    users = UserStore(tmp_path / "users.json")
    for email, role in (("admin@exemple.sn", "admin"), ("it@exemple.sn", "ecriture"),
                        ("lit@exemple.sn", "lecture")):
        users.add(email, email, policy=False, role=role, must_change=False)

    faux = FauxGlpi()
    import printwatch.glpi as module_glpi
    monkeypatch.setattr(module_glpi, "Glpi", lambda cfg: faux)

    app = create_app(db, users=users, manage="open",
                     glpi=GlpiConfig(url="https://glpi.example/api.php",
                                     client_id="c", username="u"))

    def as_(email):
        c = TestClient(app, base_url="https://testserver")
        assert c.post("/api/auth/login", json={"email": email, "password": email},
                      headers=ORIGIN).status_code == 200
        return c

    return {"as": as_, "db": db, "glpi": faux}


def _technicien(client, nom, email=""):
    r = client.post("/api/technicians", json={"name": nom, "glpi_email": email},
                    headers=ORIGIN)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── Qui a le droit de regarder ──────────────────────────────────────────────────

def test_le_role_lecture_ne_voit_pas_les_tickets(env):
    """La liste porte les intitulés des demandes de tout le personnel, parfois un nom ou
    un message d'erreur. « lecture » existe pour regarder des niveaux d'encre."""
    assert env["as"]("lit@exemple.sn").get("/api/tickets").status_code == 403


def test_sans_configuration_la_route_REFUSE(tmp_path):
    """Un écran vide en silence ferait croire qu'il n'y a rien à faire."""
    db = str(tmp_path / "t.db")
    Storage(db).close()
    users = UserStore(tmp_path / "users.json")
    users.add("it@exemple.sn", "it@exemple.sn", policy=False, role="ecriture", must_change=False)
    c = TestClient(create_app(db, users=users, manage="open"),
                   base_url="https://testserver")
    c.post("/api/auth/login", json={"email": "it@exemple.sn", "password": "it@exemple.sn"},
           headers=ORIGIN)
    r = c.get("/api/tickets")
    assert r.status_code == 503
    assert "GLPI" in r.json()["detail"]


# ── La colonne « confié à » ─────────────────────────────────────────────────────

def test_un_ticket_sans_attributaire_est_libre(env):
    tickets = env["as"]("it@exemple.sn").get("/api/tickets").json()["tickets"]
    assert [t["confie_a"] for t in tickets] == [None, None]


def test_glpi_fait_foi_quand_il_connait_l_attributaire(env):
    env["glpi"].assignes = {5532: ["Technicien Un"]}
    tickets = env["as"]("it@exemple.sn").get("/api/tickets").json()["tickets"]
    par_id = {t["id"]: t for t in tickets}
    assert par_id[5532]["confie_a"] == "Technicien Un"
    assert par_id[5532]["hors_glpi"] is False


def test_SANS_compte_glpi_la_table_locale_est_la_seule_reponse(env):
    """LE TEST QUI COMPTE : sans lui, l'écran afficherait « libre » sur un ticket
    pourtant confié, et personne ne le reprendrait."""
    admin = env["as"]("admin@exemple.sn")
    tid = _technicien(admin, "Stagiaire Exemple")           # pas d'adresse : hors GLPI
    it = env["as"]("it@exemple.sn")
    assert it.post("/api/tickets/5532/confier", json={"technician_id": tid},
                   headers=ORIGIN).status_code == 200

    par_id = {t["id"]: t for t in it.get("/api/tickets").json()["tickets"]}
    assert par_id[5532]["confie_a"] == "Stagiaire Exemple"
    assert par_id[5532]["hors_glpi"] is True
    # ET LA TRACE EST DANS LE TICKET : c'est la seule qu'on puisse y écrire.
    assert env["glpi"].suivis and env["glpi"].suivis[0][0] == 5532
    assert "Stagiaire Exemple" in env["glpi"].suivis[0][1]


def test_glpi_passe_devant_la_table_locale(env):
    """Les deux peuvent répondre ; GLPI fait foi, parce qu'il est la source que les
    techniciens voient dans leur propre outil."""
    admin = env["as"]("admin@exemple.sn")
    tid = _technicien(admin, "Stagiaire Exemple")
    it = env["as"]("it@exemple.sn")
    it.post("/api/tickets/5532/confier", json={"technician_id": tid}, headers=ORIGIN)
    env["glpi"].assignes = {5532: ["Quelqu'un dans GLPI"]}

    par_id = {t["id"]: t for t in it.get("/api/tickets").json()["tickets"]}
    assert par_id[5532]["confie_a"] == "Quelqu'un dans GLPI"
    assert par_id[5532]["hors_glpi"] is False


# ── Confier ─────────────────────────────────────────────────────────────────────

def test_avec_un_compte_glpi_l_attribution_y_est_ecrite(env):
    env["glpi"].compte = 42
    admin = env["as"]("admin@exemple.sn")
    tid = _technicien(admin, "Technicien Un", "technicien.un@exemple.sn")
    it = env["as"]("it@exemple.sn")
    r = it.post("/api/tickets/5532/confier", json={"technician_id": tid}, headers=ORIGIN)
    assert r.status_code == 200
    assert r.json()["hors_glpi"] is False
    assert env["glpi"].attributions == [(5532, 42)]
    # Pas de suivi : l'attribution est dans GLPI, le redire serait du bruit.
    assert env["glpi"].suivis == []


def test_une_adresse_qui_n_existe_pas_dans_glpi_est_REFUSEE(env):
    """Plutôt que de confier en silence hors GLPI : le technicien a une adresse, donc
    quelqu'un a voulu qu'il soit dans GLPI. L'écart se corrige, il ne se contourne pas."""
    env["glpi"].compte = None
    admin = env["as"]("admin@exemple.sn")
    tid = _technicien(admin, "Fantome", "fantome@exemple.sn")
    r = env["as"]("it@exemple.sn").post("/api/tickets/5532/confier",
                                    json={"technician_id": tid}, headers=ORIGIN)
    assert r.status_code == 409
    assert "fantome@exemple.sn" in r.json()["detail"]


def test_un_refus_de_glpi_n_enregistre_RIEN(env):
    """L'ordre compte : GLPI d'abord, la table ensuite. Un ticket qu'on croit confié et
    que personne ne traite est pire qu'un refus affiché."""
    env["glpi"].compte = 42
    env["glpi"].echoue_attribution = True
    admin = env["as"]("admin@exemple.sn")
    tid = _technicien(admin, "Technicien Un", "technicien.un@exemple.sn")
    it = env["as"]("it@exemple.sn")
    assert it.post("/api/tickets/5532/confier", json={"technician_id": tid},
                   headers=ORIGIN).status_code == 502

    par_id = {t["id"]: t for t in it.get("/api/tickets").json()["tickets"]}
    assert par_id[5532]["confie_a"] is None


def test_un_technicien_retire_ne_recoit_plus(env):
    admin = env["as"]("admin@exemple.sn")
    tid = _technicien(admin, "Parti")
    admin.patch(f"/api/technicians/{tid}", json={"active": False}, headers=ORIGIN)
    r = env["as"]("it@exemple.sn").post("/api/tickets/5532/confier",
                                    json={"technician_id": tid}, headers=ORIGIN)
    assert r.status_code == 404


def test_confier_de_nouveau_remplace(env):
    admin = env["as"]("admin@exemple.sn")
    a = _technicien(admin, "Premier")
    b = _technicien(admin, "Second")
    it = env["as"]("it@exemple.sn")
    it.post("/api/tickets/5532/confier", json={"technician_id": a}, headers=ORIGIN)
    it.post("/api/tickets/5532/confier", json={"technician_id": b}, headers=ORIGIN)
    par_id = {t["id"]: t for t in it.get("/api/tickets").json()["tickets"]}
    assert par_id[5532]["confie_a"] == "Second"


# ── Le filtre de l'écran ────────────────────────────────────────────────────────

def test_le_filtre_intranet_ne_garde_que_les_siens(env):
    tickets = env["as"]("it@exemple.sn").get(
        "/api/tickets", params={"intranet": "true"}).json()["tickets"]
    assert [t["id"] for t in tickets] == [5532]


# ── Le carnet ───────────────────────────────────────────────────────────────────

def test_seul_un_admin_compose_le_carnet(env):
    # LE NOM EST VALIDE EXPRÈS. FastAPI valide le corps AVANT d'entrer dans la fonction,
    # donc un corps invalide rend 422 et la garde de rôle ne s'exécute jamais : un test
    # écrit avec un nom trop court vérifierait la validation en croyant vérifier le rôle.
    # C'est l'ordre de tout le projet — `require()` est appelé dans le corps du gestionnaire.
    r = env["as"]("it@exemple.sn").post("/api/technicians", json={"name": "Quelqu un"},
                                    headers=ORIGIN)
    assert r.status_code == 403


def test_deux_techniciens_ne_portent_pas_le_meme_nom(env):
    admin = env["as"]("admin@exemple.sn")
    _technicien(admin, "Technicien Un")
    r = admin.post("/api/technicians", json={"name": "Technicien Un"}, headers=ORIGIN)
    assert r.status_code == 409


def test_les_inactifs_restent_servis_par_defaut(env):
    """Un technicien retiré reste l'attributaire de ses anciens tickets : le masquer
    ferait disparaître un nom sans rien expliquer."""
    admin = env["as"]("admin@exemple.sn")
    tid = _technicien(admin, "Parti")
    admin.patch(f"/api/technicians/{tid}", json={"active": False}, headers=ORIGIN)
    tous = admin.get("/api/technicians").json()
    actifs = admin.get("/api/technicians", params={"actifs": "true"}).json()
    assert [t["name"] for t in tous] == ["Parti"]
    assert actifs == []


def test_effacer_l_adresse_fait_repasser_hors_glpi(env):
    """Les trois états de `glpi_email` : absent, une adresse, la chaîne vide."""
    admin = env["as"]("admin@exemple.sn")
    tid = _technicien(admin, "Technicien Un", "technicien.un@exemple.sn")
    admin.patch(f"/api/technicians/{tid}", json={"name": "Michel C."}, headers=ORIGIN)
    assert admin.get("/api/technicians").json()[0]["glpi_email"] == "technicien.un@exemple.sn"
    admin.patch(f"/api/technicians/{tid}", json={"glpi_email": ""}, headers=ORIGIN)
    assert admin.get("/api/technicians").json()[0]["glpi_email"] is None
