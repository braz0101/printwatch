"""API HTTP + tableau de bord (fichier statique unique).

Lecture : connexion SQLite en lecture seule. Écriture (gestion des imprimantes) : routes dédiées, désactivées hors boucle locale
sauf si PRINTWATCH_ADMIN_TOKEN est défini (voir `manage`).
"""
from __future__ import annotations

import hmac
import re
import sqlite3
import time
import unicodedata
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import ConfigError, check_host
from .model import Printer
from .series import down_intervals, downsample, fleet_daily, group_samples, replacements, uptime_pct
from .storage import Storage
from .sync import SyncController

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
    elif now - last > 2 * interval_s + 120:
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
               manage: str = "off", admin_token: str | None = None, allowed_hosts: list[str] | None = None) -> FastAPI:
    """manage : `off` (lecture seule), `open` (gestion libre : usage local) ou `token` (gestion avec en-tête X-Admin-Token).

    allowed_hosts : noms d'hôte acceptés dans l'en-tête Host (None : pas de contrôle, cas d'un service exposé derrière un proxy)."""
    app = FastAPI(title="PrintWatch", docs_url=None, redoc_url=None)
    app.add_middleware(SecurityHeaders)
    if allowed_hosts:
        app.add_middleware(HostGuard, allowed=allowed_hosts)
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

    @app.get("/api/status")
    def status() -> dict:
        now, st = clock(), open_db()
        printers: dict[str, dict] = {}
        try:
            cyc = st.last_cycle()
            for r in st.overview():
                p = printers.setdefault(r["printer_id"], {
                    "id": r["printer_id"], "name": r["name"], "brand": r["brand"], "model": r["model"],
                    "location": r["location"], "user": r["user"], "down": bool(r["down"]), "last_seen": r["last_seen"],
                    "error": r["last_error"], "supplies": [],
                    **({"host": r["host"]} if manage != "off" else {})})
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
                                      "alert": LEVELS[r["alert_level"]], "forecast": fc, "stale": stale})
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
                "capabilities": {"sync": sync is not None, "manage": manage}, "printers": ordered, "thresholds": {"warn": warn, "crit": crit},
                "summary": {"printers": len(ordered), "down": by_state("down"), "crit": by_state("CRIT"),
                            "warn": by_state("WARN"), "ok": by_state("OK"), "nodata": by_state("NODATA")}}

    # --- écriture : synchronisation et gestion des imprimantes ---------------------------------------------
    def same_origin(request: Request) -> None:
        """Un navigateur joint `Origin` aux requêtes d'écriture : refuse celles venues d'une autre page (CSRF). curl n'en envoie pas."""
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            raise HTTPException(403, "origine refusée")

    def admin(request: Request) -> None:
        if manage == "off":
            raise HTTPException(403, "gestion des imprimantes désactivée sur ce serveur")
        same_origin(request)
        if manage == "token" and not (admin_token and hmac.compare_digest(request.headers.get("x-admin-token", ""), admin_token)):
            raise HTTPException(401, "code administrateur requis")

    def open_rw() -> Storage:
        try:
            return Storage(db_path)
        except sqlite3.OperationalError:
            raise HTTPException(503, "base de données en lecture seule ou indisponible")

    @app.post("/api/sync", status_code=202)
    def sync_now(request: Request) -> dict:
        same_origin(request)
        if sync is None:
            raise HTTPException(501, "collecte non intégrée à ce serveur (lancer `serve --collect`)")
        return {"state": sync.request()}

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
            st.update_printer(printer_id, **fields)
            out = public(st.get_printer(printer_id))                  # type: ignore[arg-type]
        finally:
            st.close()
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
        finally:
            st.close()
        out = fleet_daily(rows, [(p["id"], p["name"]) for p in printers], spans, now, days, warn, crit)
        out["thresholds"] = {"warn": warn, "crit": crit}
        return out

    return app
