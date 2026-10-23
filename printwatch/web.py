"""API HTTP + tableau de bord (fichier statique unique).

Accès : avec un `UserStore`, toute l'API exige une session ouverte par /api/auth/login (seuls la page, son bundle et la connexion
restent publics : ils ne contiennent aucune donnée). Droits par rôle : lecture (consulter), ecriture (synchroniser, incidents,
imprimantes), admin (comptes, paramètres). Sans comptes (démo locale), gestion selon `manage` (ouverte ou code administrateur).
Lecture : connexion SQLite en lecture seule ; écritures sur des routes dédiées, avec connexion courte distincte.
"""
from __future__ import annotations

import csv
import hmac
import html
import io
import logging
import re
import sqlite3
import time
import unicodedata
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .alerts import SUPPLY_GROUPS, AlertSettings, Thresholds
from .config import GlpiConfig
from .auth import REMEMBER_TTL, ROLES, AuthError, Throttle, UserStore, allows, normalize_email
from .config import ConfigError, check_host
from .model import Printer
from .series import down_intervals, downsample, fleet_daily, group_samples, replacements, uptime_pct
from .storage import Storage
from .sync import SyncController

log = logging.getLogger("printwatch.web")
DAY = 86400.0
LEVELS = ("OK", "WARN", "CRIT")
STATIC = Path(__file__).parent / "static"
INDEX = STATIC / "index.html"


# Aucune ressource externe : scripts, styles, polices et images viennent tous de l'application elle-même.
# `style-src 'unsafe-inline'` ne vaut que pour les attributs style="" posés par React (jamais de <script> en ligne).
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                                "font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response


def _hostname(host_header: str) -> str:
    """Nom d'hôte d'un en-tête Host, sans port : `localhost:8080` → `localhost`, `[::1]:8080` → `[::1]`."""
    h = host_header.strip().lower()
    if h.startswith("["):
        return h[: h.find("]") + 1]
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


class HostGuard(BaseHTTPMiddleware):
    """Refuse les requêtes dont l'en-tête Host n'est pas attendu. Parade au « rebinding DNS » : sans elle, une page web malveillante dont le
    nom pointerait vers 127.0.0.1 pourrait atteindre ce serveur local avec une origine cohérente et appeler les routes d'écriture."""

    def __init__(self, app, allowed: list[str]) -> None:
        super().__init__(app)
        self.allowed = {a.lower() for a in allowed}

    async def dispatch(self, request, call_next):
        if _hostname(request.headers.get("host", "")) not in self.allowed:
            return PlainTextResponse("en-tête Host non autorisé (voir PRINTWATCH_ALLOWED_HOSTS)", status_code=400)
        return await call_next(request)


SESSION_COOKIE = "pw_session"
PUBLIC_PATHS = {"/", "/api/auth/login", "/api/auth/me", "/api/health"}
# Mot de passe provisoire : tant qu'il n'est pas remplacé, la session ne sert QU'À le remplacer (le provisoire a pu être lu
# ou entendu par d'autres ; il n'ouvre donc ni les données ni les tickets).
MUST_CHANGE_PATHS = {"/api/auth/me", "/api/auth/password", "/api/auth/logout"}


def set_session_cookie(response, token: str, ttl: int, remember: bool, secure: bool) -> None:
    """HttpOnly (illisible en JavaScript), Secure (HTTPS seulement), SameSite=Strict (jamais joint par un autre site).
    Sans « rester connecté » : cookie de navigateur, oublié à la fermeture ; la signature porte de toute façon sa propre échéance."""
    response.set_cookie(SESSION_COOKIE, token, max_age=ttl if remember else None, path="/", httponly=True, secure=secure, samesite="strict")


def clear_session_cookie(response, secure: bool) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=secure, samesite="strict")


class AuthGuard(BaseHTTPMiddleware):
    """Refuse (401) toute requête hors pages publiques sans session valide. Renouvelle les sessions « rester connecté » à mi-vie :
    un écran mural qui rafraîchit ses données ne se déconnecte jamais."""

    def __init__(self, app, users: UserStore, secure: bool) -> None:
        super().__init__(app)
        self.users, self.secure = users, secure

    async def dispatch(self, request, call_next):
        path = request.url.path
        session = self.users.verify(request.cookies.get(SESSION_COOKIE))
        request.state.session = session
        if session is None and path not in PUBLIC_PATHS and not path.startswith("/assets/"):
            return JSONResponse({"detail": "connexion requise"}, status_code=401, headers={"Cache-Control": "no-store"})
        if session is not None and path.startswith("/api/") and path not in PUBLIC_PATHS | MUST_CHANGE_PATHS:
            if (self.users.get(session.email) or {}).get("must_change"):
                return JSONResponse({"detail": "remplacez d'abord votre mot de passe provisoire", "code": "must_change"},
                                    status_code=403, headers={"Cache-Control": "no-store"})
        response = await call_next(request)
        if (session and session.remember and session.expires - self.users.clock() < REMEMBER_TTL / 2
                and path != "/api/auth/logout" and "set-cookie" not in response.headers):
            token, ttl = self.users.issue(session.email, True)
            set_session_cookie(response, token, ttl, True, self.secure)
        return response


def client_key(request) -> str:
    """Poste à l'origine de la requête. Derrière IIS/ARR (pair = boucle locale) : dernière adresse ajoutée à X-Forwarded-For par le proxy."""
    peer = request.client.host if request.client else ""
    last = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
    if peer in ("127.0.0.1", "::1") and last:
        return last.rsplit(":", 1)[0] if last.count(":") == 1 else last          # ARR peut ajouter le port : « 10.0.0.5:51234 »
    return peer


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=254)
    password: str = Field(max_length=1024)
    remember: bool = False


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current: str = Field(max_length=1024)
    new: str = Field(max_length=1024)


class PasswordReset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(max_length=1024)


class UserIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=254)
    password: str = Field(max_length=1024)
    role: str = "ecriture"


INCIDENT_CATEGORIES = ("Bourrage papier", "Panne matérielle", "Consommable", "Réseau / connexion", "Qualité d'impression", "Autre")
_CTRL_TEXT = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")         # texte libre : retours à la ligne et tabulations admis


class IncidentIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    category: str = Field(max_length=40)
    description: str = Field(min_length=1, max_length=1000)

    @field_validator("category")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in INCIDENT_CATEGORIES:
            raise ValueError("type d'incident inconnu")
        return v

    @field_validator("description")
    @classmethod
    def _clean(cls, v: str) -> str:
        if _CTRL_TEXT.search(v):
            raise ValueError("caractères de contrôle interdits")
        return v


def _clean_name(v: str | None) -> str | None:
    if v is not None and _CTRL.search(v):
        raise ValueError("caractères de contrôle interdits")
    if v is not None and re.search(r"[<>]", v):
        raise ValueError("les caractères < et > sont interdits")
    return v


def _clean_glpi_email(v: str | None) -> str | None:
    """Vide (hors GLPI) ou une adresse valide, normalisée."""
    if v is None or not v.strip():
        return v if v is None else ""
    try:
        return normalize_email(v)
    except AuthError as exc:
        raise ValueError(str(exc)) from exc


class TechnicienIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=2, max_length=120)
    # FACULTATIF, ET C'EST LA RÈGLE DU MODULE : on doit pouvoir inscrire quelqu'un qui n'a
    # pas de compte GLPI. L'attribution devient alors un suivi dans la chronologie du
    # ticket, et l'attributaire affiché est marqué « hors GLPI ».
    glpi_email: str = Field(default="", max_length=190)

    @field_validator("name")
    @classmethod
    def check_name(cls, v):
        return _clean_name(v)

    @field_validator("glpi_email")
    @classmethod
    def check_glpi_email(cls, v):
        return _clean_glpi_email(v)


class TechnicienPatch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str | None = Field(default=None, min_length=2, max_length=120)
    # TROIS ÉTATS, et les confondre coûterait cher : absent (on n'y touche pas), une
    # adresse (on la pose), la chaîne vide (on l'efface, et le technicien repasse « hors
    # GLPI »).
    glpi_email: str | None = Field(default=None, max_length=190)
    active: bool | None = None

    @field_validator("name")
    @classmethod
    def check_name(cls, v):
        return _clean_name(v)

    @field_validator("glpi_email")
    @classmethod
    def check_glpi_email(cls, v):
        return _clean_glpi_email(v)


class ConfierIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    technician_id: int


class ResolveIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    resolution: str = Field("", max_length=1000)

    @field_validator("resolution")
    @classmethod
    def _clean(cls, v: str) -> str:
        if _CTRL_TEXT.search(v):
            raise ValueError("caractères de contrôle interdits")
        return v


class RoleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str

    @field_validator("role")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in ROLES:
            raise ValueError("rôle inconnu (lecture, ecriture ou admin)")
        return v


class SettingsIn(BaseModel):
    """Seuils (en % restant) et consommables qui déclenchent des alertes : black, cyan, magenta, yellow, box, other."""
    model_config = ConfigDict(extra="forbid")
    warn: float = Field(ge=0, le=100)
    crit: float = Field(ge=0, le=100)
    inks: list[str] = Field(min_length=1, max_length=len(SUPPLY_GROUPS))


class ReadOnlyStorage(Storage):
    """Même requêtes que Storage, mais connexion SQLite en lecture seule : l'API ne peut rien modifier."""

    def __init__(self, path: str) -> None:
        uri = f"file:{Path(path).resolve().as_posix()}?mode=ro"
        self.conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row


def collector_health(cyc, printers: list[dict], now: float, interval_s: int) -> dict:
    """Santé de la collecte, déduite du dernier cycle enregistré (repli : dernier relevé réussi d'une imprimante).

    ok = cycle récent ; stale = aucun cycle depuis plus de 2 intervalles (collecteur arrêté) ; never = rien n'a jamais été relevé ;
    all_failed = le dernier cycle n'a obtenu de réponse d'aucune imprimante (réseau, communauté/identifiants SNMP…).
    """
    seen = [p["last_seen"] for p in printers if p["last_seen"]]
    last = cyc["ts"] if cyc else (max(seen) if seen else None)
    polled, failed = (cyc["polled"], cyc["failed"]) if cyc else (None, None)
    if last is None:
        state = "never"
    elif interval_s > 0 and now - last > 2 * interval_s + 120:          # interval_s = 0 : relevés à la demande, jamais « à l'arrêt »
        state = "stale"
    elif polled and failed == polled:
        state = "all_failed"
    else:
        state = "ok"
    return {"last_cycle": last, "polled": polled, "failed": failed, "interval_s": interval_s, "state": state}


def printer_state(p: dict) -> str:
    """État global d'une imprimante : down > CRIT > WARN > OK ; NODATA tant qu'aucun relevé n'existe."""
    if p["down"]:
        return "down"
    if not p["supplies"]:
        return "NODATA"
    live = [s for s in p["supplies"] if not s["stale"]]          # un consommable fantôme ne doit pas entretenir une alerte
    if not live:
        return "NODATA"
    levels = {s["alert"] for s in live}
    return "CRIT" if "CRIT" in levels else "WARN" if "WARN" in levels else "OK"


_CTRL = re.compile(r"[\x00-\x1f\x7f]")


class PrinterIn(BaseModel):
    """Fiche d'imprimante saisie depuis le dashboard (texte brut, sans caractères de contrôle)."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    host: str = Field(min_length=1, max_length=253)
    brand: str = Field("", max_length=40)
    model: str = Field("", max_length=60)
    location: str = Field("", max_length=120)
    user: str = Field("", max_length=80)

    @field_validator("*")
    @classmethod
    def _no_control_chars(cls, v: str) -> str:
        if _CTRL.search(v):
            raise ValueError("caractères de contrôle interdits")
        return v


class PrinterPatch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str | None = Field(None, min_length=1, max_length=80)
    host: str | None = Field(None, min_length=1, max_length=253)
    brand: str | None = Field(None, max_length=40)
    model: str | None = Field(None, max_length=60)
    location: str | None = Field(None, max_length=120)
    user: str | None = Field(None, max_length=80)

    @field_validator("*")
    @classmethod
    def _no_control_chars(cls, v: str | None) -> str | None:
        if v is not None and _CTRL.search(v):
            raise ValueError("caractères de contrôle interdits")
        return v


def new_printer_id(host: str, name: str, taken: set[str]) -> str:
    """imp-<3e>-<4e octet> pour une IPv4 (convention du parc), sinon un identifiant dérivé du nom ; unique par suffixe."""
    m = re.fullmatch(r"\d+\.\d+\.(\d+)\.(\d+)", host)
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()      # « é » → « e »
    base = f"imp-{m[1]}-{m[2]}" if m else "imp-" + (re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-") or "sans-nom")
    pid, n = base, 2
    while pid in taken:
        pid, n = f"{base}-{n}", n + 1
    return pid


def create_app(db_path: str, *, replacement_jump: float = 20.0, warn: float = 15.0, crit: float = 5.0,
               interval_s: int = 1800, clock: Callable[[], float] = time.time, sync: SyncController | None = None,
               manage: str = "off", admin_token: str | None = None, allowed_hosts: list[str] | None = None,
               users: UserStore | None = None, throttle: Throttle | None = None, cookie_secure: bool = True,
               glpi: "GlpiConfig | None" = None) -> FastAPI:
    """manage : `off` (gestion des imprimantes coupée), `open` (selon le rôle du compte ; libre sans comptes) ou `token` (sans comptes :
    en-tête X-Admin-Token).

    allowed_hosts : noms d'hôte acceptés dans l'en-tête Host (None : pas de contrôle, cas d'un service exposé derrière un proxy).
    users : comptes autorisés (None : pas d'authentification, cas de la démo) ; throttle : blocage après échecs de connexion.

    glpi : lien vers le serveur de tickets (None : les routes de répartition répondent 503).
    PASSÉE ET NON LUE D'UN SINGLETON, comme tout le reste de cette fonction : une
    installation de PrintWatch qui ne gère pas de tickets doit démarrer sans section
    [glpi], et un test doit pouvoir en fournir une fausse sans toucher au disque."""
    app = FastAPI(title="PrintWatch", docs_url=None, redoc_url=None)
    # Le dernier ajouté est le plus externe : SecurityHeaders enveloppe tout, y compris les refus de HostGuard et d'AuthGuard.
    if users is not None:
        app.add_middleware(AuthGuard, users=users, secure=cookie_secure)
    limiter = throttle or Throttle(clock=clock)
    if allowed_hosts:
        app.add_middleware(HostGuard, allowed=allowed_hosts)
    app.add_middleware(SecurityHeaders)
    if (STATIC / "assets").is_dir():          # bundle JS/CSS produit par `npm run build` (noms hachés)
        app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    def open_db() -> ReadOnlyStorage:
        try:
            return ReadOnlyStorage(db_path)
        except sqlite3.OperationalError:
            raise HTTPException(503, "base de données indisponible (aucun cycle de collecte lancé ?)")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(INDEX, headers={"Cache-Control": "no-store"})

    defaults = AlertSettings(warn, crit)                 # config.toml, tant que rien n'est réglé depuis le dashboard

    def audit(request: Request, action: str, target: str = "", user: str | None = None) -> None:
        """Trace une action (qui, quoi, sur quoi, d'où). Jamais de mot de passe ni de jeton. Un échec d'écriture ne bloque pas l'action."""
        try:
            st = Storage(db_path)
        except sqlite3.Error:
            log.warning("journal d'audit indisponible (%s)", action)
            return
        try:
            st.audit(clock(), user or author(request), action, target, client_key(request))
        except sqlite3.Error:
            log.warning("journal d'audit : écriture impossible (%s)", action)
        finally:
            st.close()

    def role_for(request: Request) -> str | None:
        """Rôle du compte connecté ; None sans authentification (démo locale : tout est permis, sous réserve de `manage`)."""
        if users is None:
            return None
        session = getattr(request.state, "session", None)
        return users.role(session.email) if session else None

    def can(request: Request, needed: str) -> bool:
        return users is None or allows(role_for(request), needed)

    def require(request: Request, needed: str) -> None:
        """Écriture : même origine web (anti-CSRF) ET rôle suffisant, vérifié côté serveur à chaque requête."""
        same_origin(request)
        if not can(request, needed):
            label = {"ecriture": "écriture", "admin": "administrateur"}[needed]
            raise HTTPException(403, f"droits insuffisants : rôle « {label} » requis")

    def level_of(settings: AlertSettings, kind: str, color: str | None, pct: float | None) -> str:
        """Alerte AFFICHÉE, recalculée avec les réglages courants : un changement de seuil se voit aussitôt, sans attendre un relevé.
        (Les courriels, eux, suivent l'état du collecteur, avec hystérésis.)"""
        if pct is None or not settings.tracks(kind, color):
            return "OK"
        return LEVELS[int(Thresholds(settings.warn, settings.crit).raw_level(pct))]

    @app.get("/api/status")
    def status(request: Request) -> dict:
        show_hosts = manage != "off" and can(request, "ecriture")      # un compte « lecture » ne voit pas les adresses IP
        now, st = clock(), open_db()
        printers: dict[str, dict] = {}
        try:
            settings = st.alert_settings(defaults)
            cyc = st.last_cycle()
            for r in st.overview():
                p = printers.setdefault(r["printer_id"], {
                    "id": r["printer_id"], "name": r["name"], "brand": r["brand"], "model": r["model"],
                    "location": r["location"], "user": r["user"], "down": bool(r["down"]), "last_seen": r["last_seen"],
                    "error": r["last_error"], "supplies": [],
                    **({"host": r["host"]} if show_hosts else {})})
                if r["key"] is None:
                    continue
                # Périmé : imprimante injoignable, OU consommable que le dernier relevé réussi n'a pas rafraîchi
                # (disparu de la table SNMP après un changement de firmware ou de cartouche : « fantôme »).
                last_seen = r["last_seen"]
                stale = bool(r["down"]) or (r["updated_at"] is not None and last_seen is not None and r["updated_at"] < last_seen - 1.0)
                fc = ({"days_left": round(r["fc_days"], 1), "confidence": r["fc_conf"]}
                      if r["fc_days"] is not None and r["pct"] is not None and not stale else None)
                p["supplies"].append({"key": r["key"], "description": r["description"], "kind": r["kind"],
                                      "color": r["color"], "status": r["status"], "pct": r["pct"],
                                      "alert": level_of(settings, r["kind"], r["color"], r["pct"]),
                                      "tracked": settings.tracks(r["kind"], r["color"]), "forecast": fc, "stale": stale})
            infos, pages, open_inc = st.device_infos(), st.pages_since(now - 30 * DAY), st.open_incidents()
            for pid, p in printers.items():
                p["info"] = infos.get(pid)
                p["pages_30d"] = pages.get(pid)
                p["incidents"] = [{k: i[k] for k in ("id", "category", "description", "opened_at", "opened_by")} for i in open_inc.get(pid, [])]
        finally:
            st.close()

        def urgency(p: dict) -> tuple[int, float]:
            pcts = [s["pct"] for s in p["supplies"] if s["pct"] is not None]
            return (0 if p["down"] else 1, min(pcts) if pcts else 101.0)

        ordered = sorted(printers.values(), key=urgency)
        for p in ordered:
            p["supplies"].sort(key=lambda s: (s["pct"] is None, s["pct"] or 0))
            p["state"] = printer_state(p)
        by_state = lambda st_: sum(1 for p in ordered if p["state"] == st_)  # noqa: E731
        # Synthèse PAR IMPRIMANTE (même unité que le badge des cartes) : les catégories sont exclusives
        # et leur somme vaut `printers`. Une imprimante injoignable n'est comptée que comme telle.
        return {"generated_at": now, "collector": {**collector_health(cyc, ordered, now, interval_s), **({"running": sync.running} if sync else {})},
                "capabilities": {"sync": sync is not None and can(request, "ecriture"),
                                 "manage": manage if can(request, "ecriture") else "off",
                                 "incidents": can(request, "ecriture"), "admin": can(request, "admin"), "role": role_for(request)},
                "printers": ordered, "thresholds": settings.as_dict(),
                "summary": {"printers": len(ordered), "down": by_state("down"), "crit": by_state("CRIT"),
                            "warn": by_state("WARN"), "ok": by_state("OK"), "nodata": by_state("NODATA")}}

    # --- écriture : synchronisation et gestion des imprimantes ---------------------------------------------
    def same_origin(request: Request) -> None:
        """Un navigateur joint `Origin` aux requêtes d'écriture : refuse celles venues d'une autre page (CSRF). curl n'en envoie pas."""
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            raise HTTPException(403, "origine refusée")

    def admin(request: Request) -> None:
        """Gestion des imprimantes : rôle « écriture » avec des comptes ; sans comptes, mode `open` ou code administrateur."""
        if manage == "off":
            raise HTTPException(403, "gestion des imprimantes désactivée sur ce serveur")
        require(request, "ecriture")
        if users is None and manage == "token" and not (admin_token and hmac.compare_digest(request.headers.get("x-admin-token", ""), admin_token)):
            raise HTTPException(401, "code administrateur requis")

    def open_rw() -> Storage:
        try:
            return Storage(db_path)
        except sqlite3.OperationalError:
            raise HTTPException(503, "base de données en lecture seule ou indisponible")

    @app.post("/api/sync", status_code=202)
    def sync_now(request: Request) -> dict:
        require(request, "ecriture")
        if sync is None:
            raise HTTPException(501, "collecte non intégrée à ce serveur (lancer `serve --collect`)")
        state = sync.request()
        audit(request, "sync", state)
        return {"state": state}

    def host_or_422(host: str) -> str:
        try:
            return check_host(host)
        except ConfigError as exc:
            raise HTTPException(422, str(exc))

    def public(p: Printer) -> dict:
        return {"id": p.id, "name": p.name, "host": p.host, "brand": p.brand, "model": p.model, "location": p.location, "user": p.user}

    @app.post("/api/printers", status_code=201)
    def add_printer(body: PrinterIn, request: Request) -> dict:
        admin(request)
        host = host_or_422(body.host)
        st = open_rw()
        try:
            same = st.printer_by_host(host)
            if same and same.enabled:
                raise HTTPException(409, f"l'adresse {host} est déjà suivie ({same.name})")
            fields = {"name": body.name, "host": host, "brand": body.brand, "model": body.model, "location": body.location, "user": body.user}
            if same:                                                  # retirée auparavant : on la remet en supervision avec son historique
                st.update_printer(same.id, **fields)
                st.set_enabled(same.id, True)
                out = {**public(st.get_printer(same.id)), "restored": True}      # type: ignore[arg-type]
            else:
                pid = new_printer_id(host, body.name, {p.id for p in st.list_printers()})
                st.upsert_printer(Printer(id=pid, enabled=True, **fields))
                out = {**public(st.get_printer(pid)), "restored": False}         # type: ignore[arg-type]
        finally:
            st.close()
        audit(request, "printer_restore" if out["restored"] else "printer_add", f"{out['id']} ({out['name']}, {out['host']})")
        if sync:
            sync.request(queue=True)                                  # premier relevé sans attendre le prochain cycle
        return out

    @app.patch("/api/printers/{printer_id}")
    def edit_printer(printer_id: str, body: PrinterPatch, request: Request) -> dict:
        admin(request)
        fields = body.model_dump(exclude_none=True)
        if "host" in fields:
            fields["host"] = host_or_422(fields["host"])
        st = open_rw()
        try:
            cur = st.get_printer(printer_id)
            if cur is None or not cur.enabled:
                raise HTTPException(404, "imprimante inconnue")
            if "host" in fields:
                same = st.printer_by_host(fields["host"])
                if same and same.id != printer_id and same.enabled:
                    raise HTTPException(409, f"l'adresse {fields['host']} est déjà suivie ({same.name})")
            changes = [f"{k} : « {getattr(cur, k)} » → « {v} »" for k, v in fields.items() if getattr(cur, k) != v]
            st.update_printer(printer_id, **fields)
            out = public(st.get_printer(printer_id))                  # type: ignore[arg-type]
        finally:
            st.close()
        if changes:
            audit(request, "printer_edit", f"{printer_id} ({cur.name}) — " + " ; ".join(changes))
        if sync and "host" in fields:
            sync.request(queue=True)
        return out

    @app.delete("/api/printers/{printer_id}")
    def remove_printer(printer_id: str, request: Request) -> dict:
        """Retire de la supervision (l'historique est conservé ; ajouter la même adresse la remet en service)."""
        admin(request)
        st = open_rw()
        try:
            cur = st.get_printer(printer_id)
            if cur is None or not cur.enabled:
                raise HTTPException(404, "imprimante inconnue")
            st.set_enabled(printer_id, False)
        finally:
            st.close()
        audit(request, "printer_remove", f"{printer_id} ({cur.name})")
        return {"archived": True, "id": printer_id}

    @app.get("/api/history/{printer_id}/{key}")
    def history(printer_id: str, key: str, days: int = Query(30, ge=1, le=400)) -> list[dict]:
        st = open_db()
        try:
            return [{"ts": ts, "pct": pct} for ts, pct in st.history(printer_id, key, clock() - days * DAY)]
        finally:
            st.close()

    @app.get("/api/events")
    def events(limit: int = Query(30, ge=1, le=200)) -> list[dict]:
        st = open_db()
        try:
            return [{"ts": r["ts"], "type": r["type"], "printer": r["printer_name"], "message": r["message"],
                     "notified": bool(r["notified"])} for r in st.recent_events(limit)]
        finally:
            st.close()


    @app.get("/api/series")
    def series(days: int = Query(30, ge=1, le=400), points: int = Query(60, ge=8, le=400),
               printer: str | None = Query(None, max_length=64)) -> dict:
        """Toutes les séries de niveau du parc en UNE requête (sparklines, grand graphique), rééchantillonnées.

        `pts` : [[fin de seau (epoch s), % restant], ...] sur une grille commune ; `replaced` : dates de remplacement
        détectées (saut vers le haut d'au moins `replacement_jump` points sur les relevés bruts).
        """
        until = clock()
        since = until - days * DAY
        st = open_db()
        try:
            grouped = group_samples(st.samples_since(since, printer))
        finally:
            st.close()
        return {"since": since, "until": until, "step": (until - since) / points,
                "series": [{"printer": pid, "key": key, "pts": downsample(pts, since, until, points),
                            "replaced": replacements(pts, replacement_jump)}
                           for (pid, key), pts in sorted(grouped.items())]}

    @app.get("/api/availability")
    def availability(days: int = Query(30, ge=1, le=400)) -> dict:
        """Périodes d'injoignabilité par imprimante, reconstruites depuis les événements down/up (pas de sondage dédié)."""
        until = clock()
        since = until - days * DAY
        st = open_db()
        try:
            printers = st.printers_basic()
            spans = down_intervals(st.availability_events(), [(p["id"], p["down"], p["last_seen"]) for p in printers],
                                   since, until)
        finally:
            st.close()
        return {"since": since, "until": until,
                "printers": [{"id": p["id"], "name": p["name"], "down_now": bool(p["down"]),
                              "uptime_pct": uptime_pct(spans[p["id"]], since, until),
                              "down": [[round(a), round(b)] for a, b in spans[p["id"]]]} for p in printers]}

    @app.get("/api/fleet/daily")
    def fleet_daily_view(days: int = Query(30, ge=1, le=400)) -> dict:
        """État de chaque imprimante, jour par jour (jours UTC), et totaux du parc : heatmap et tendance."""
        now = clock()
        st = open_db()
        try:
            printers = st.printers_basic()
            first_day = (int(now // DAY) - days + 1) * DAY
            spans = down_intervals(st.availability_events(), [(p["id"], p["down"], p["last_seen"]) for p in printers],
                                   first_day, now)
            rows = st.samples_since(first_day)
            settings = st.alert_settings(defaults)
        finally:
            st.close()
        out = fleet_daily(rows, [(p["id"], p["name"]) for p in printers], spans, now, days, settings.warn, settings.crit)
        out["thresholds"] = {"warn": settings.warn, "crit": settings.crit}
        return out

    # --- incidents saisis sur une imprimante -------------------------------------------------------------------------
    def author(request: Request) -> str:
        session = getattr(request.state, "session", None)
        return session.email if session else "local"

    @app.get("/api/incidents/categories")
    def incident_categories() -> list[str]:
        return list(INCIDENT_CATEGORIES)

    @app.get("/api/printers/{printer_id}/incidents")
    def list_incidents(printer_id: str) -> list[dict]:
        st = open_db()
        try:
            try:
                return st.incidents(printer_id)
            except sqlite3.OperationalError:          # base pas encore migrée (service non redémarré)
                return []
        finally:
            st.close()

    @app.post("/api/printers/{printer_id}/incidents", status_code=201)
    def add_incident(printer_id: str, body: IncidentIn, request: Request) -> dict:
        """Déclare un incident (rôle « écriture ») : auteur et date enregistrés, ouvert jusqu'à sa clôture."""
        require(request, "ecriture")
        st = open_rw()
        try:
            cur = st.get_printer(printer_id)
            if cur is None or not cur.enabled:
                raise HTTPException(404, "imprimante inconnue")
            iid = st.add_incident(printer_id, body.category, body.description, clock(), author(request))
            out = st.get_incident(iid)
        finally:
            st.close()
        audit(request, "incident_add", f"n° {iid} sur {printer_id} ({body.category})")
        return out                                                         # type: ignore[return-value]

    @app.post("/api/incidents/{incident_id}/resolve")
    def resolve_incident(incident_id: int, body: ResolveIn, request: Request) -> dict:
        require(request, "ecriture")
        st = open_rw()
        try:
            cur = st.get_incident(incident_id)
            if cur is None:
                raise HTTPException(404, "incident inconnu")
            if cur["resolved_at"] is not None:
                raise HTTPException(409, "incident déjà clôturé")
            st.resolve_incident(incident_id, clock(), author(request), body.resolution)
            out = st.get_incident(incident_id)
        finally:
            st.close()
        audit(request, "incident_resolve", f"n° {incident_id} sur {cur['printer_id']}")
        return out                                                         # type: ignore[return-value]

    # --- répartition des tickets d'assistance -----------------------------------------------------------------------
    #
    # POURQUOI ICI ET PLUS SUR L'INTRANET (21/10/2026) : l'intranet est l'outil de tout le
    # personnel, PrintWatch celui du service informatique. Déposer une demande y reste —
    # c'est là que sont les agents ; la répartir est un geste de l'équipe.
    #
    # LA LECTURE EXIGE « écriture », pas « lecture ». Ce n'est pas une sévérité gratuite :
    # la liste porte les intitulés des demandes de tout le personnel, parfois un nom ou un
    # message d'erreur. Le rôle « lecture » existe pour regarder des niveaux d'encre.

    _client_glpi: list = []        # un seul élément : le client, construit au premier appel

    def _glpi():
        """Le client, ou un refus explicite. Jamais un écran vide en silence.

        CONSTRUIT UNE SEULE FOIS, et c'est ce qui donne son effet au cache de jeton : un
        client neuf par requête repart d'un cache vide, donc redemande un jeton OAuth —
        deux par chargement d'écran, indéfiniment. Le client garde le jeton une heure et
        le renouvelle de lui-même sur un 401, donc rien ne se périme en mémoire.

        LES SECRETS NE SONT PAS RELUS à chaque appel non plus. Conséquence à connaître :
        un secret corrigé dans le coffre ne prend effet qu'au redémarrage du service.
        C'est le cas de tous les réglages de `config.toml`, et `printwatch glpi` lit le
        coffre à chaque fois — donc le dépannage ne dépend pas de ce cache.
        """
        from .glpi import Glpi
        if glpi is None:
            raise HTTPException(503, "GLPI n'est pas configuré sur ce serveur : "
                                     "voir la section [glpi] et le coffre de secrets.")
        if not _client_glpi:
            _client_glpi.append(Glpi(glpi))
        g = _client_glpi[0]
        if not g.est_actif():
            raise HTTPException(503, "GLPI n'est pas configuré sur ce serveur : "
                                     "voir la section [glpi] et le coffre de secrets.")
        return g

    @app.get("/api/tickets")
    def tickets(request: Request, limite: int = Query(60, ge=1, le=200), ouverts: bool = True,
                intranet: bool = False) -> dict:
        """Les tickets ouverts, avec qui les tient.

        DEUX SOURCES POUR UNE SEULE COLONNE « confié à », et il faut les deux : GLPI fait
        foi quand l'attributaire y a un compte ; sinon, seule la table locale sait à qui
        le ticket a été confié. Un écran qui ne lirait que GLPI afficherait « libre » sur
        un ticket pourtant attribué.
        """
        require(request, "ecriture")
        from .glpi import ErreurGlpi
        g = _glpi()
        try:
            lignes = g.lister_tickets(limite=limite, ouverts_seulement=ouverts)
        except ErreurGlpi as e:
            raise HTTPException(502, str(e)) from e

        if intranet:
            lignes = [t for t in lignes if t.get("depuis_intranet")]

        numeros = [t["id"] for t in lignes]
        dans_glpi = g.attributaires(numeros)

        st = open_db()
        try:
            try:
                locales = st.assignments(numeros)
            except sqlite3.OperationalError:      # base pas encore migrée (service non redémarré)
                locales = {}
        finally:
            st.close()

        for t in lignes:
            noms = [n for n in dans_glpi.get(t["id"], []) if n]
            local = locales.get(t["id"])
            if noms:
                t["confie_a"] = ", ".join(noms)
                t["hors_glpi"] = False
            elif local:
                t["confie_a"] = local["name"]
                t["hors_glpi"] = bool(local["hors_glpi"])
            else:
                t["confie_a"] = None
                t["hors_glpi"] = False
        return {"tickets": lignes, "total": len(lignes)}

    @app.post("/api/tickets/{numero}/confier")
    def confier(numero: int, body: ConfierIn, request: Request) -> dict:
        """Confie un ticket. Écrit dans GLPI quand c'est possible, trace toujours.

        L'ORDRE COMPTE : GLPI d'abord, la table locale ensuite. Si GLPI refuse, rien n'est
        enregistré et l'écran le dit — un ticket qu'on croit confié et que personne ne
        traite est pire qu'un refus affiché.
        """
        require(request, "ecriture")
        from .glpi import ErreurGlpi
        g = _glpi()

        st = open_rw()
        try:
            tech = st.technician(body.technician_id)
            if tech is None or not tech["active"]:
                raise HTTPException(404, "Ce technicien n'existe pas, ou ne reçoit plus "
                                         "de tickets.")

            dans_glpi = False
            if tech["glpi_email"]:
                identifiant = g.utilisateur_par_email(tech["glpi_email"])
                if identifiant is None:
                    raise HTTPException(
                        409, f"Aucun compte GLPI ne porte l'adresse "
                             f"{tech['glpi_email']}. Corrigez-la, ou laissez-la vide pour "
                             f"confier hors GLPI.")
                try:
                    g.attribuer(numero, identifiant)
                except ErreurGlpi as e:
                    raise HTTPException(502, f"GLPI a refusé l'attribution : {e}") from e
                dans_glpi = True
            else:
                # SANS COMPTE GLPI, LE SUIVI EST LA SEULE TRACE dans le ticket lui-même.
                # Il n'est pas fatal : l'attribution locale vaut, et un suivi qui manque
                # ne la défait pas.
                g.ajouter_suivi(
                    numero,
                    f"Ticket confié à {html.escape(tech['name'])} depuis PrintWatch.")      # contenu HTML côté GLPI

            st.assign_ticket(numero, tech["id"], dans_glpi, clock(), author(request))
        finally:
            st.close()
        audit(request, "ticket_assign", f"n° {numero} → {tech['name']}" + ("" if dans_glpi else " (hors GLPI)"))
        return {"ticket": numero, "confie_a": tech["name"], "hors_glpi": not dans_glpi}

    @app.get("/api/technicians")
    def list_technicians(request: Request, actifs: bool = False) -> list[dict]:
        """Le carnet.

        LES INACTIFS SONT SERVIS par défaut : un technicien retiré reste l'attributaire de
        ses anciens tickets, et la colonne « confié à » doit pouvoir le nommer.
        """
        require(request, "ecriture")
        st = open_db()
        try:
            try:
                return st.technicians(actifs_seulement=actifs)
            except sqlite3.OperationalError:
                return []
        finally:
            st.close()

    @app.post("/api/technicians", status_code=201)
    def add_technician(body: TechnicienIn, request: Request) -> dict:
        require(request, "admin")
        st = open_rw()
        try:
            try:
                tid = st.add_technician(body.name.strip(), body.glpi_email.strip() or None,
                                        clock(), author(request))
            except sqlite3.IntegrityError as e:
                raise HTTPException(409, "Un technicien porte déjà ce nom.") from e
            out = st.technician(tid)
        finally:
            st.close()
        audit(request, "technician_add", f"{body.name.strip()}" + (f" <{body.glpi_email}>" if body.glpi_email else " (hors GLPI)"))
        return out                                                      # type: ignore[return-value]

    @app.patch("/api/technicians/{technician_id}")
    def patch_technician(technician_id: int, body: TechnicienPatch,
                         request: Request) -> dict:
        require(request, "admin")
        st = open_rw()
        try:
            if st.technician(technician_id) is None:
                raise HTTPException(404, "technicien inconnu")
            try:
                st.update_technician(
                    technician_id,
                    name=body.name.strip() if body.name is not None else None,
                    glpi_email=(body.glpi_email.strip()
                                if body.glpi_email is not None else None),
                    active=body.active)
            except sqlite3.IntegrityError as e:
                raise HTTPException(409, "Un technicien porte déjà ce nom.") from e
            out = st.technician(technician_id)
        finally:
            st.close()
        champs = body.model_dump(exclude_none=True)
        audit(request, "technician_edit", f"n° {technician_id} ({out['name']}) — " + ", ".join(f"{k} = {v}" for k, v in champs.items()))
        return out                                                      # type: ignore[return-value]

    # --- réglages des alertes ---------------------------------------------------------------------------------------
    @app.get("/api/settings")
    def get_settings() -> dict:
        st = open_db()
        try:
            return {**st.alert_settings(defaults).as_dict(), "choices": list(SUPPLY_GROUPS)}
        finally:
            st.close()

    @app.put("/api/settings")
    def put_settings(body: SettingsIn, request: Request) -> dict:
        """Seuils et consommables suivis (administrateurs). Affichage mis à jour aussitôt ; courriels dès la prochaine synchronisation."""
        require(request, "admin")
        try:
            new = AlertSettings(body.warn, body.crit, frozenset(body.inks))
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        st = open_rw()
        try:
            st.set_alert_settings(new)
        finally:
            st.close()
        noms = {"black": "noir", "cyan": "cyan", "magenta": "magenta", "yellow": "jaune", "box": "boîtier de maintenance", "other": "autres"}
        audit(request, "settings", f"attention {new.warn:g} %, critique {new.crit:g} %, suivis : "
                                   + ", ".join(noms.get(g, g) for g in new.as_dict()["inks"]))
        return {**new.as_dict(), "choices": list(SUPPLY_GROUPS)}

    # --- comptes et session ------------------------------------------------------------------------------------------
    @app.get("/api/health", include_in_schema=False)
    def health() -> dict:
        """Sonde publique (script d'installation, supervision) : ne révèle rien du parc."""
        return {"ok": True}

    def need_users() -> UserStore:
        if users is None:
            raise HTTPException(404, "authentification désactivée sur ce serveur")
        return users

    def session_of(request: Request):
        session = getattr(request.state, "session", None)
        if session is None:
            raise HTTPException(401, "connexion requise")
        return session

    def me_payload(email: str) -> dict:
        u = need_users().get(email) or {}
        return {"auth": True, "email": email, "role": need_users().role(email), "must_change": bool(u.get("must_change"))}

    def wait_message(seconds: int) -> str:
        return f"trop de tentatives : réessayez dans {max(1, round(seconds / 60))} min"

    @app.get("/api/auth/me")
    def me(request: Request) -> JSONResponse:
        if users is None:
            return JSONResponse({"auth": False, "email": None, "role": None, "must_change": False})
        return JSONResponse(me_payload(session_of(request).email), headers={"Cache-Control": "no-store"})

    @app.post("/api/auth/login")
    def login(body: LoginIn, request: Request) -> JSONResponse:
        """Échecs comptés par compte ET par poste : 5 en 15 min bloquent 15 min (même réponse pour un compte inconnu)."""
        same_origin(request)
        store = need_users()
        keys = (f"user:{body.email.strip().lower()}", f"ip:{client_key(request)}")
        wait = limiter.retry_after(*keys)
        if wait:
            raise HTTPException(429, wait_message(wait), headers={"Retry-After": str(wait)})
        who = store.authenticate(body.email, body.password)
        if who is None:
            limiter.failure(*keys)
            # Une adresse inconnue n'est pas recopiée : c'est parfois un mot de passe tapé dans le mauvais champ.
            known = store.get(body.email) is not None
            audit(request, "login_failed", "", user=body.email.strip().lower() if known else "(inconnu)")
            raise HTTPException(401, "adresse ou mot de passe incorrect")
        limiter.success(*keys)
        audit(request, "login_ok", "rester connecté" if body.remember else "", user=who)
        token, ttl = store.issue(who, body.remember)
        response = JSONResponse(me_payload(who), headers={"Cache-Control": "no-store"})
        set_session_cookie(response, token, ttl, body.remember, cookie_secure)
        return response

    @app.post("/api/auth/logout")
    def logout(request: Request) -> JSONResponse:
        same_origin(request)
        if users is not None:
            session = getattr(request.state, "session", None)
            users.revoke(request.cookies.get(SESSION_COOKIE))           # le jeton cesse d'être valable, même copié
            if session:
                audit(request, "logout", "", user=session.email)
        response = JSONResponse({"ok": True})
        clear_session_cookie(response, cookie_secure)
        return response

    @app.post("/api/auth/password")
    def change_password(body: PasswordChange, request: Request) -> JSONResponse:
        """Change son propre mot de passe (l'actuel est exigé). Les autres sessions du compte sont fermées, celle-ci est renouvelée."""
        same_origin(request)
        store, session = need_users(), session_of(request)
        key = f"user:{session.email}"
        wait = limiter.retry_after(key)
        if wait:
            raise HTTPException(429, wait_message(wait), headers={"Retry-After": str(wait)})
        if store.authenticate(session.email, body.current) is None:
            limiter.failure(key)
            audit(request, "password_change_failed", "mot de passe actuel incorrect")
            raise HTTPException(403, "mot de passe actuel incorrect")
        if body.new == body.current:
            raise HTTPException(422, "le nouveau mot de passe doit être différent de l'actuel")
        try:
            store.set_password(session.email, body.new, must_change=False)
        except AuthError as exc:
            raise HTTPException(422, str(exc))
        audit(request, "password_change", session.email)
        token, ttl = store.issue(session.email, session.remember)
        response = JSONResponse(me_payload(session.email))
        set_session_cookie(response, token, ttl, session.remember, cookie_secure)
        return response

    @app.get("/api/users")
    def list_users(request: Request) -> list[dict]:
        store = need_users()
        session_of(request)
        if not can(request, "admin"):
            raise HTTPException(403, "droits insuffisants : rôle « administrateur » requis")
        return store.list()

    @app.post("/api/users", status_code=201)
    def add_user(body: UserIn, request: Request) -> dict:
        """Nouveau compte, avec un mot de passe provisoire à changer à la première connexion."""
        need_users()
        session_of(request)
        require(request, "admin")
        try:
            email = need_users().add(body.email, body.password, must_change=True, role=body.role)
        except AuthError as exc:
            raise HTTPException(409 if "existe déjà" in str(exc) else 422, str(exc))
        audit(request, "user_add", f"{email} ({body.role})")
        return {"email": email}

    @app.delete("/api/users/{email}")
    def remove_user(email: str, request: Request) -> dict:
        store, session = need_users(), session_of(request)
        require(request, "admin")
        if email.strip().lower() == session.email:
            raise HTTPException(409, "impossible de retirer votre propre compte")
        if store.get(email) is None:
            raise HTTPException(404, "compte inconnu")
        try:
            store.remove(email)
        except AuthError as exc:
            raise HTTPException(409, str(exc))
        audit(request, "user_remove", email.strip().lower())
        return {"removed": email.strip().lower()}

    @app.patch("/api/users/{email}/role")
    def set_role(email: str, body: RoleIn, request: Request) -> dict:
        """Change les droits d'un AUTRE compte (administrateurs) ; effet immédiat, même sur ses sessions ouvertes."""
        store, session = need_users(), session_of(request)
        require(request, "admin")
        if email.strip().lower() == session.email:
            raise HTTPException(409, "impossible de modifier vos propres droits")
        if store.get(email) is None:
            raise HTTPException(404, "compte inconnu")
        before = store.role(email)
        try:
            store.set_role(email, body.role)
        except AuthError as exc:
            raise HTTPException(409, str(exc))
        audit(request, "user_role", f"{email.strip().lower()} : {before} → {body.role}")
        return {"email": email.strip().lower(), "role": body.role}

    @app.post("/api/users/{email}/password")
    def reset_password(email: str, body: PasswordReset, request: Request) -> dict:
        """Mot de passe provisoire pour un AUTRE compte : ses sessions ouvertes sont fermées, il devra le changer à la connexion."""
        store, session = need_users(), session_of(request)
        require(request, "admin")
        if email.strip().lower() == session.email:
            raise HTTPException(409, "pour votre propre compte, utilisez « Changer mon mot de passe »")
        if store.get(email) is None:
            raise HTTPException(404, "compte inconnu")
        try:
            store.set_password(email, body.password, must_change=True)
        except AuthError as exc:
            raise HTTPException(422, str(exc))
        audit(request, "user_reset", email.strip().lower())
        return {"email": email.strip().lower(), "must_change": True}

    # --- journal d'audit (administrateurs) -------------------------------------------------------------------------
    def audit_rows(request: Request, limit: int, user: str | None, action: str | None, before: int | None,
                   since: float | None) -> list[dict]:
        need_users()
        session_of(request)
        if not can(request, "admin"):
            raise HTTPException(403, "droits insuffisants : rôle « administrateur » requis")
        st = open_db()
        try:
            return st.recent_audit(limit, user=user, action=action, before_id=before, since=since)
        finally:
            st.close()

    @app.get("/api/audit")
    def audit_list(request: Request, limit: int = Query(100, ge=1, le=1000), user: str | None = Query(None, max_length=254),
                   action: str | None = Query(None, max_length=40), before: int | None = Query(None, ge=1),
                   since: float | None = None) -> dict:
        rows = audit_rows(request, limit + 1, user, action, before, since)
        return {"entries": rows[:limit], "more": len(rows) > limit}

    @app.get("/api/audit.csv")
    def audit_csv(request: Request, user: str | None = Query(None, max_length=254), action: str | None = Query(None, max_length=40),
                  since: float | None = None) -> Response:
        from datetime import datetime
        rows = audit_rows(request, 10000, user, action, None, since)
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
        w.writerow(["date", "compte", "action", "détail", "poste"])
        for r in rows:
            cells = [datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S"), r["user"], r["action"], r["target"], r["source"]]
            w.writerow(["'" + c if c[:1] in ("=", "+", "-", "@") else c for c in cells])      # pas de formule exécutée par Excel
        return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="printwatch-journal.csv"', "Cache-Control": "no-store"})

    return app
