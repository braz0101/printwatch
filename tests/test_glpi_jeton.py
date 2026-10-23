# -*- coding: utf-8 -*-
"""Le jeton OAuth : garde, renouvele sur un 401, et jamais demande deux fois pour rien.

CES TESTS NE REMPLACENT QUE `_appel`, la prise reseau. C'est la lecon d'un test rate
ailleurs : remplacer la methode ou vit la mecanique qu'on pretend verifier, c'est ne
verifier que son propre faux. `_jeton`, `_entetes` et `_lire` sont donc les vrais.
"""
from __future__ import annotations

import pytest

from printwatch.config import GlpiConfig
from printwatch.glpi import ErreurGlpi, Glpi

SECRETS = {"client_secret": "s3cr3t", "password": "p4ss"}


def _config() -> GlpiConfig:
    return GlpiConfig(url="https://glpi.example.org/api.php", client_id="cid",
                      username="svc", entity_id=0)


class Prise:
    """La prise reseau, et elle compte ce qui passe par elle."""

    def __init__(self, *, duree=3600, refus_401=0):
        self.appels: list[tuple] = []
        self.duree = duree
        self.refus_401 = refus_401            # nombre de 401 a rendre avant de ceder

    def __call__(self, chemin, methode="GET", entetes=None, corps=None, form=None):
        self.appels.append((chemin, methode, (entetes or {}).get("Authorization")))
        if chemin == "token":
            return {"access_token": f"jeton-{self.jetons}", "expires_in": self.duree}
        if self.refus_401 > 0:
            self.refus_401 -= 1
            raise ErreurGlpi(f"GLPI a répondu 401 sur {chemin} : expired")
        return []

    @property
    def jetons(self) -> int:
        return sum(1 for c in self.appels if c[0] == "token")

    @property
    def lectures(self) -> int:
        return sum(1 for c in self.appels if c[0] != "token")


def _client(prise) -> Glpi:
    g = Glpi(_config(), secrets=dict(SECRETS))
    g._appel = prise                                      # seule la prise est fausse
    return g


def test_deux_lectures_ne_demandent_QU_UN_jeton():
    """LE DÉFAUT CORRIGÉ LE 21/10 : sans cache, chaque lecture en redemandait un."""
    prise = Prise()
    g = _client(prise)
    g._lire("v2/Assistance/Ticket")
    g._lire("v2/Assistance/Ticket")
    assert prise.jetons == 1
    assert prise.lectures == 2


def test_le_jeton_voyage_dans_l_en_tete():
    prise = Prise()
    g = _client(prise)
    g._lire("v2/Assistance/Ticket")
    lecture = [c for c in prise.appels if c[0] != "token"][0]
    assert lecture[2] == "Bearer jeton-1"


def test_un_401_renouvelle_le_jeton_et_REJOUE_l_appel():
    """Un jeton révoqué avant notre échéance ne doit pas condamner le service."""
    prise = Prise(refus_401=1)
    g = _client(prise)
    assert g._lire("v2/Assistance/Ticket") == []
    assert prise.jetons == 2                              # le premier, puis le neuf
    assert prise.lectures == 2                            # l'appel refusé, puis le rejoué
    rejoue = [c for c in prise.appels if c[0] != "token"][-1]
    assert rejoue[2] == "Bearer jeton-2"


def test_DEUX_401_de_suite_ne_sont_pas_un_jeton_perime():
    """Une seule reprise : deux refus de suite sont un refus, pas une expiration.

    Sans cette borne, un compte désactivé côté GLPI ferait tourner la reprise sans fin.
    """
    prise = Prise(refus_401=5)
    g = _client(prise)
    with pytest.raises(ErreurGlpi):
        g._lire("v2/Assistance/Ticket")
    assert prise.jetons == 2


def test_une_erreur_qui_n_est_PAS_un_401_ne_renouvelle_rien():
    """Un 404 ou un 500 ne se soignent pas par un jeton neuf."""
    prise = Prise()
    g = _client(prise)

    def refuse(chemin, methode="GET", entetes=None, corps=None, form=None):
        if chemin == "token":
            return {"access_token": "jeton-1", "expires_in": 3600}
        raise ErreurGlpi("GLPI a répondu 500 sur truc : boum")

    g._appel = refuse
    with pytest.raises(ErreurGlpi, match="500"):
        g._lire("v2/Assistance/Ticket")


def test_un_jeton_a_bout_de_course_est_redemande():
    """LA MARGE DE 60 s : un jeton qui expire pendant le trajet donnerait un 401 nu."""
    prise = Prise(duree=70)                               # 70 - 60 = 10 s de validité utile
    g = _client(prise)
    g._lire("v2/Assistance/Ticket")
    g._jeton_jusqua = 0.0                                 # comme si les 10 s étaient passées
    g._lire("v2/Assistance/Ticket")
    assert prise.jetons == 2


def test_une_duree_absurde_est_ramenee_a_une_heure():
    """Un serveur annonçant une semaine nous ferait garder un jeton révoqué."""
    prise = Prise(duree=7 * 24 * 3600)
    g = _client(prise)
    g._lire("v2/Assistance/Ticket")
    import time
    assert g._jeton_jusqua - time.monotonic() <= 3600


def test_une_duree_illisible_ne_fait_pas_echouer_l_appel():
    prise = Prise(duree="beaucoup")
    g = _client(prise)
    assert g._lire("v2/Assistance/Ticket") == []
    assert prise.jetons == 1


def test_le_pool_des_attributaires_partage_UN_jeton():
    """Dix appels de front, un seul jeton : c'est le verrou qui le garantit."""
    prise = Prise()
    g = _client(prise)
    g.attributaires(list(range(1, 31)))
    assert prise.jetons == 1
    assert prise.lectures == 30


# ── Le client vit aussi longtemps que le service ────────────────────────────────

def test_le_service_ne_construit_le_client_QU_UNE_fois(tmp_path, monkeypatch):
    """SANS CELA LE CACHE NE SERVIRAIT A RIEN : un client neuf par requete repart d'un
    cache vide, donc redemande un jeton. Un ecran rafraichi chaque minute, c'etaient deux
    connexions du compte de service par minute dans les journaux de GLPI."""
    from fastapi.testclient import TestClient

    from printwatch.auth import UserStore
    from printwatch.storage import Storage
    from printwatch.web import create_app
    import printwatch.glpi as module_glpi

    db = str(tmp_path / "t.db")
    Storage(db).close()
    users = UserStore(tmp_path / "users.json")
    users.add("it@exemple.sn", "it@exemple.sn", policy=False, role="ecriture", must_change=False)

    constructions = []

    class Faux:
        def __init__(self, cfg):
            constructions.append(cfg)

        def est_actif(self):
            return True

        def lister_tickets(self, limite=60, ouverts_seulement=True):
            return []

        def attributaires(self, numeros):
            return {}

    monkeypatch.setattr(module_glpi, "Glpi", Faux)
    app = create_app(db, users=users, manage="open",
                     glpi=_config())
    c = TestClient(app, base_url="https://testserver")
    entetes = {"Origin": "https://testserver"}
    assert c.post("/api/auth/login",
                  json={"email": "it@exemple.sn", "password": "it@exemple.sn"},
                  headers=entetes).status_code == 200
    for _ in range(3):
        assert c.get("/api/tickets").status_code == 200
    assert len(constructions) == 1
