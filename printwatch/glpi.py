"""
Client GLPI — lire les tickets d'assistance et les confier.

POURQUOI CE MODULE EST ICI. Les tickets se répartissaient depuis l'intranet, qui est
l'outil de tout le personnel ; depuis le 21/10/2026 ils se gèrent dans PrintWatch, où
seules les personnes du service informatique ont un compte. Déposer une demande reste sur
l'intranet — c'est là que sont les agents.

CE QU'IL NE FAIT PAS, ET C'EST VOULU : il ne crée pas de ticket. La création vit sur
l'intranet, avec le formulaire qui l'alimente, et la dupliquer ici ferait deux chemins
d'écriture vers GLPI à tenir d'accord. Ce module lit et attribue, rien d'autre.

L'API DE GLPI 11. L'ancienne API (`apirest.php`, jetons `App-Token` et `user_token`) est
désactivée sur le serveur de production. La nouvelle est versionnée sous `api.php` et
authentifiée en OAuth 2.0 :

    POST {url}/token                                   flux `password` -> access_token (1 h)
    GET  {url}/v2/Assistance/Ticket                    la liste
    GET  {url}/v2/Assistance/Ticket/{id}/TeamMember/assigned   qui le tient
    POST {url}/v2/Assistance/Ticket/{id}/TeamMember    attribuer
    GET  {url}/v2/Administration/User                  filtre RSQL sur l'adresse
    POST {url}/v2/Assistance/Ticket/{id}/Followup      écrire un suivi

LE JETON EST GARDÉ EN MÉMOIRE jusqu'à son expiration (une heure au plus, voir `_jeton`),
jamais sur disque : sans ce cache, chaque chargement d'écran coûtait deux authentifications
du compte de service. Il est renouvelé une fois sur `401`.

DEUX CHOSES APPRISES EN INTERROGEANT LE SERVEUR DE PRODUCTION, et qui ne se devinent pas :
1. Un `Content-Type: application/json` posé sur une requête **sans corps** fait répondre
   « Contenu du JSON invalide » : GLPI tente de lire un corps vide. L'en-tête n'est donc
   ajouté que lorsqu'il y a quelque chose à envoyer.
2. Le rôle d'un acteur vient d'une énumération — `requester`, `assigned`, `observer` — et
   non d'un entier comme dans l'ancienne API.

Bibliothèque standard uniquement, comme le reste du cœur de PrintWatch.
"""

from __future__ import annotations

import html
import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from .config import GlpiConfig

logger = logging.getLogger(__name__)

# Portées demandées au jeton : `api` ouvre l'API, `user` et `email` autorisent la lecture
# des comptes et de leurs adresses, dont dépend la recherche d'un technicien.
PORTEES = "api user email"

# Caractères que le langage de filtre RSQL interprète. Une adresse qui en contiendrait
# fausserait la requête : on renonce à chercher plutôt que d'envoyer un filtre douteux.
RSQL_INTERDITS = set(";,()=\"'<>!~ ")

STATUTS = {1: "Nouveau", 10: "Validation", 2: "En cours", 3: "Planifié",
           4: "En attente", 5: "Résolu", 6: "Clos"}

# « Ouvert » veut dire tout sauf résolu et clos.
STATUTS_FERMES = (5, 6)

# LE MARQUEUR DES TICKETS NÉS DANS L'INTRANET : la phrase que l'intranet écrit au pied de
# chaque demande (« Demande déposée depuis l'intranet … »). Plusieurs fragments, TOUS exigés,
# parce que le mot « intranet » seul ne suffit pas : filtrer dessus ramenait des courriels
# transférés qui le mentionnaient au passage. La comparaison se fait sur un texte « à plat »
# (entités HTML décodées, accents retirés, minuscules) : voir `a_plat`. Réglable dans
# `[glpi] marqueurs_intranet`.
MARQUEURS_INTRANET = ("deposee depuis", "intranet")


def a_plat(texte) -> str:
    """Texte comparable : entités HTML décodées (GLPI stocke du HTML), accents retirés, apostrophes unifiées, minuscules."""
    t = html.unescape(str(texte or "")).replace("\u2019", "'")
    return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii").lower()

# Bornes de la liste : chaque ticket listé coûte ensuite un appel à GLPI (qui le tient ?).
LIMITE_MIN, LIMITE_MAX = 1, 200


class ErreurGlpi(Exception):
    """Échec de dialogue avec GLPI.

    ELLE EST FATALE ICI, à la différence de l'intranet où un échec GLPI se rattrape par un
    courriel. Un écran de répartition n'a rien à rattraper : s'il affichait une liste vide
    en silence, il ferait croire qu'il n'y a rien à faire.
    """


class Glpi:
    """Le dialogue avec un serveur GLPI, pour une configuration donnée.

    UNE CLASSE ET NON DES FONCTIONS DE MODULE, à la différence de l'intranet : là-bas la
    configuration est un singleton Django lisible de partout. Ici elle est passée, ce qui
    rend le module testable sans monter la moitié de l'application — et c'est aussi la
    forme qu'ont déjà `Storage` et `UserStore`.
    """

    def __init__(self, cfg: GlpiConfig, secrets: dict[str, str] | None = None):
        self.cfg = cfg
        self._secrets = secrets
        # LE JETON SE GARDE, il vaut une heure. Sans ce cache, chaque chargement d'écran
        # coûtait deux authentifications par mot de passe du compte de service — la liste,
        # puis le pool d'`attributaires` —, et le client étant reconstruit à chaque requête
        # rien ne se gardait d'une fois sur l'autre : un écran rafraîchi chaque minute,
        # c'étaient deux connexions par minute dans les journaux de GLPI.
        self._jeton_cache: str | None = None
        self._jeton_jusqua: float = 0.0
        # Le verrou existe POUR LE POOL DE `attributaires` : dix fils partant ensemble sur
        # un cache vide demanderaient dix jetons, ce qui est precisement le defaut corrige.
        self._verrou = threading.Lock()

    # ── Le transport ────────────────────────────────────────────────────────────

    def est_actif(self) -> bool:
        """Vrai si tout est là pour appeler. Un seul réglage manquant désactive tout."""
        if not self.cfg.est_configure():
            return False
        try:
            self.secrets()
        except Exception:
            return False
        return True

    def secrets(self) -> dict[str, str]:
        if self._secrets is None:
            self._secrets = self.cfg.secrets()
        return self._secrets

    def _appel(self, chemin: str, methode: str = "GET", entetes: dict | None = None,
               corps: dict | None = None, form: dict | None = None):
        base = self.cfg.url.rstrip("/")
        url = f"{base}/{chemin.lstrip('/')}"

        donnees = type_contenu = None
        if form is not None:
            donnees = urllib.parse.urlencode(form).encode("utf-8")
            type_contenu = "application/x-www-form-urlencoded"
        elif corps is not None:
            donnees = json.dumps(corps).encode("utf-8")
            type_contenu = "application/json"

        requete = urllib.request.Request(url, data=donnees, method=methode)
        # Pas de Content-Type sans corps : GLPI essaierait de lire un JSON absent.
        if type_contenu:
            requete.add_header("Content-Type", type_contenu)
        for cle, valeur in (entetes or {}).items():
            requete.add_header(cle, valeur)

        try:
            with urllib.request.urlopen(requete, timeout=self.cfg.timeout) as reponse:
                brut = reponse.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            # GLPI décrit ses refus dans le corps : le relire évite un message opaque.
            detail = e.read().decode("utf-8", "replace")[:300] if e.fp else ""
            raise ErreurGlpi(f"GLPI a répondu {e.code} sur {chemin} : {detail}") from e
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            raise ErreurGlpi(f"GLPI injoignable ({chemin}) : {e}") from e

        if not brut.strip():
            return None
        try:
            return json.loads(brut)
        except json.JSONDecodeError as e:
            raise ErreurGlpi(f"Réponse GLPI illisible sur {chemin} : {brut[:200]}") from e

    def _jeton(self, force: bool = False) -> str:
        """Jeton d'accès OAuth du compte de service, gardé jusqu'à son expiration.

        `force` sert au seul cas qui ne se déduit pas : GLPI répond 401 alors que notre
        horloge croyait le jeton encore valable (redémarrage du serveur, révocation).
        """
        with self._verrou:
            if not force and self._jeton_cache and time.monotonic() < self._jeton_jusqua:
                return self._jeton_cache
            jeton, duree = self._demander_un_jeton()
            # UNE MARGE DE 60 s : sans elle, un jeton expirant pendant le trajet de la
            # requete donnerait un 401 que rien n'explique.
            self._jeton_cache = jeton
            self._jeton_jusqua = time.monotonic() + max(60.0, duree - 60.0)
            return jeton

    def _demander_un_jeton(self) -> tuple:
        s = self.secrets()
        reponse = self._appel("token", methode="POST", form={
            "grant_type": "password",
            "client_id": self.cfg.client_id,
            "client_secret": s["client_secret"],
            "username": self.cfg.username,
            "password": s["password"],
            "scope": PORTEES,
        })
        jeton = (reponse or {}).get("access_token")
        if not jeton:
            raise ErreurGlpi("GLPI n'a pas renvoyé de jeton d'accès.")
        # `expires_in` est en secondes ; GLPI 11 annonce 3600. On ne lui fait pas dire
        # plus d'une heure : un serveur qui annoncerait une semaine nous ferait garder un
        # jeton revoque.
        try:
            duree = min(float((reponse or {}).get("expires_in") or 3600), 3600.0)
        except (TypeError, ValueError):
            duree = 3600.0
        return jeton, duree

    def _entetes(self, force: bool = False) -> dict:
        return {"Authorization": f"Bearer {self._jeton(force=force)}"}

    def _lire(self, chemin: str, **kw):
        """Un GET authentifié, avec UNE seule reprise sur 401.

        POURQUOI UNE REPRISE. Le jeton est gardé en mémoire ; si GLPI le révoque avant
        notre échéance (redémarrage, changement de mot de passe), toutes les requêtes
        échoueraient jusqu'au prochain démarrage du service. Une reprise, et une seule :
        deux 401 de suite ne sont pas un jeton périmé, c'est un refus.
        """
        try:
            return self._appel(chemin, entetes=self._entetes(), **kw)
        except ErreurGlpi as e:
            if " 401 " not in f" {e} ":
                raise
            return self._appel(chemin, entetes=self._entetes(force=True), **kw)

    # ── Lire ────────────────────────────────────────────────────────────────────

    def vient_de_l_intranet(self, contenu: str) -> bool:
        marqueurs = [a_plat(m) for m in (getattr(self.cfg, "marqueurs_intranet", None) or MARQUEURS_INTRANET)]
        bas = a_plat(contenu)
        return all(m in bas for m in marqueurs)

    def lister_tickets(self, limite: int = 60, ouverts_seulement: bool = True) -> list:
        """Les derniers tickets, du plus récent au plus ancien."""
        if not self.est_actif():
            raise ErreurGlpi("GLPI n'est pas configuré.")

        limite = max(LIMITE_MIN, min(int(limite), LIMITE_MAX))
        reponse = self._lire(
            f"v2/Assistance/Ticket?limit={limite}&sort=id:desc")
        bruts = reponse if isinstance(reponse, list) else []

        sortie = []
        for t in bruts:
            statut = t.get("status")
            if isinstance(statut, dict):
                statut = statut.get("id")
            if ouverts_seulement and statut in STATUTS_FERMES:
                continue
            categorie = t.get("category")
            sortie.append({
                "id": t.get("id"),
                "objet": t.get("name") or "(sans objet)",
                "date": t.get("date_creation") or t.get("date"),
                "statut": statut,
                "statut_nom": STATUTS.get(statut, str(statut)),
                "priorite": t.get("priority"),
                "categorie": categorie.get("name") if isinstance(categorie, dict) else None,
                "depuis_intranet": self.vient_de_l_intranet(t.get("content")),
            })
        return sortie

    def attributaires(self, numeros) -> dict:
        """Qui est attribué sur chacun de ces tickets : `{numéro: [noms]}`.

        DIX APPELS EN PARALLÈLE. L'information n'est pas dans la liste des tickets, il
        faut un appel par ticket, et trente appels en série mettaient plus d'une
        demi-minute — soit une demi-minute d'écran vide avant la première ligne.

        Un ticket dont la lecture échoue rend une liste vide plutôt que de faire échouer
        tout l'écran : une colonne incomplète vaut mieux qu'une page blanche.
        """
        numeros = [n for n in numeros if n]
        if not numeros:
            return {}
        entetes = self._entetes()

        def un(numero):
            try:
                a = self._appel(
                    f"v2/Assistance/Ticket/{numero}/TeamMember/assigned",
                    entetes=entetes)
            except ErreurGlpi:
                return numero, []
            if isinstance(a, dict):
                return numero, [str(v.get("display_name") or v.get("name") or "")
                                for v in a.values() if isinstance(v, dict)]
            return numero, []

        with ThreadPoolExecutor(max_workers=10) as pool:
            return dict(pool.map(un, numeros))

    def utilisateur_par_email(self, email: str) -> int | None:
        """Identifiant GLPI d'un compte, par son adresse. None s'il n'y en a pas.

        L'ADRESSE EST LA CLÉ. Elle est obligatoire dans l'annuaire Active Directory, donc
        présente pour tout compte ; et les adresses des comptes GLPI portent le drapeau
        `is_dynamic`, c'est-à-dire qu'elles viennent de ce même annuaire. Les deux
        applications lisent la même source, elles ne peuvent pas diverger. L'identifiant
        de connexion, lui, aurait pu différer selon l'import.
        """
        email = (email or "").strip()
        if not email or RSQL_INTERDITS & set(email) or not self.est_actif():
            return None
        try:
            parametres = urllib.parse.urlencode(
                {"filter": f"emails.email=={email}", "limit": 5})
            comptes = self._lire(f"v2/Administration/User?{parametres}")
        except ErreurGlpi as e:
            logger.warning("GLPI : recherche du compte %r impossible (%s)", email, e)
            return None

        if not isinstance(comptes, list) or not comptes:
            return None
        # Un compte désactivé ou supprimé ne doit pas rafler le ticket devant un compte
        # vivant qui porte la même adresse.
        comptes.sort(key=lambda c: (bool(c.get("is_deleted")), not c.get("is_active", True)))
        return comptes[0].get("id")

    # ── Écrire ──────────────────────────────────────────────────────────────────

    def attribuer(self, numero: int, glpi_user_id: int) -> None:
        """Pose l'attributaire d'un ticket.

        CELUI-CI LÈVE. L'écran doit dire que l'attribution n'a pas pris : un ticket qu'on
        croit confié et que personne ne traite est pire qu'un refus affiché.
        """
        self._appel(f"v2/Assistance/Ticket/{numero}/TeamMember", methode="POST",
                    entetes=self._entetes(),
                    corps={"type": "User", "id": int(glpi_user_id), "role": "assigned"})

    def ajouter_suivi(self, numero: int, texte_html: str, prive: bool = False) -> bool:
        """Écrit un suivi dans la chronologie du ticket. NE LÈVE JAMAIS.

        C'EST LA SEULE TRACE POSSIBLE POUR QUI N'A PAS DE COMPTE GLPI. L'API exige un
        identifiant d'utilisateur pour désigner un acteur ; sans compte il n'y a personne
        à désigner, et ce suivi est alors ce qui dit, dans le ticket lui-même, à qui il a
        été confié.

        `prive=False` par choix : le demandeur voit donc que sa demande a été prise en
        charge, ce qui est une bonne nouvelle à lui donner.

        Jamais fatal : l'attribution est faite, et un suivi qui manque ne la défait pas.
        """
        try:
            self._appel(f"v2/Assistance/Ticket/{numero}/Followup", methode="POST",
                        entetes=self._entetes(),
                        corps={"content": texte_html, "is_private": bool(prive)})
            return True
        except ErreurGlpi as e:
            logger.warning("GLPI : suivi non écrit sur le ticket %s (%s)", numero, e)
            return False
