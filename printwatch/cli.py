"""Ligne de commande : probe / matrix / once / loop / status / events."""
from __future__ import annotations

import argparse
import csv
import getpass
import json
import logging
import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Sequence

from . import __version__
from .auth import ROLES, AuthError, UserStore, normalize_email, temporary_password
from .collector import Collector
from .demo import FLEET, DemoClient
from .config import AppConfig, ConfigError, check_host, load_config, load_printers
from .forecast import estimate
from .model import Printer, Status
from .normalize import normalize
from .notify import make_notifier
from .snmp import NetSnmpClient, SnmpError
from .storage import Storage
from .sync import SyncController

log = logging.getLogger("printwatch")
DAY = 86400.0


def _table(rows: list[list[str]], headers: list[str]) -> str:
    cols = list(zip(headers, *rows)) if rows else [(h,) for h in headers]
    widths = [max(len(str(c)) for c in col) for col in cols]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    out = [fmt.format(*headers), fmt.format(*("-" * w for w in widths))]
    out += [fmt.format(*map(str, r)) for r in rows]
    return "\n".join(out)


def _fmt_pct(p: float | None) -> str:
    return "—" if p is None else f"{p:.0f} %"


# --- commandes ---------------------------------------------------------------------------------------------------
def cmd_probe(args: argparse.Namespace, cfg: AppConfig) -> int:
    host = check_host(args.host)
    client = NetSnmpClient(cfg.snmp)
    try:
        raws = client.supplies(host)
        descr = client.sys_descr(host)
    except SnmpError as exc:
        print(f"Échec : {exc}", file=sys.stderr)
        return 2
    print(f"Équipement : {descr or 'sysDescr indisponible'}")
    if not raws:
        print("Aucune table de consommables publiée (SNMP actif mais Printer MIB absente ?).")
        return 3
    rows = []
    for raw in raws:
        s = normalize(raw)
        rows.append([s.key, s.description, s.kind.value, s.status.value, _fmt_pct(s.pct_remaining),
                     "" if s.level is None else s.level, "" if s.max_capacity is None else s.max_capacity])
    print(_table(rows, ["index", "consommable", "type", "statut", "restant", "niveau brut", "capacité"]))
    return 0


def _probe_one(client: NetSnmpClient, p: Printer):
    try:
        raws = client.supplies(p.host)
        return p, raws, client.sys_descr(p.host), None
    except SnmpError as exc:
        return p, None, None, str(exc)


def cmd_matrix(args: argparse.Namespace, cfg: AppConfig) -> int:
    """Matrice de compatibilité par marque/modèle. N'écrit aucune adresse IP : le fichier peut être partagé."""
    if args.printers:                                  # CSV explicite ; sinon la liste de la base (celle du dashboard)
        printers = [p for p in load_printers(args.printers) if p.enabled]
    else:
        storage = Storage(cfg.collector.db_path)
        try:
            printers = [p for p in storage.list_printers() if p.enabled]
        finally:
            storage.close()
    client = NetSnmpClient(cfg.snmp)
    with ThreadPoolExecutor(max_workers=min(cfg.collector.workers, max(len(printers), 1))) as pool:
        results = list(pool.map(lambda p: _probe_one(client, p), printers))
    out = open(args.out, "w", newline="", encoding="utf-8") if args.out else sys.stdout
    try:
        w = csv.writer(out)
        w.writerow(["printer_id", "brand", "model", "sys_descr", "supply", "type", "color", "status",
                    "pct_remaining", "raw_level", "max_capacity", "unit"])
        for p, raws, descr, err in results:
            if raws is None:
                w.writerow([p.id, p.brand, p.model, "", "", "", "", f"unreachable: {err}", "", "", "", ""])
                continue
            for raw in raws:
                s = normalize(raw)
                w.writerow([p.id, p.brand, p.model, descr or "", s.description, s.kind.value, s.color or "",
                            s.status.value, "" if s.pct_remaining is None else s.pct_remaining,
                            "" if s.level is None else s.level,
                            "" if s.max_capacity is None else s.max_capacity,
                            "" if s.unit is None else s.unit])
    finally:
        if args.out:
            out.close()
    ok = sum(1 for _, r, _, _ in results if r)
    usable = sum(1 for _, r, _, _ in results if r and any(normalize(x).status is Status.OK for x in r))
    print(f"{ok}/{len(results)} imprimantes joignables, {usable} avec au moins un niveau exploitable.",
          file=sys.stderr)
    return 0


def _build_collector(cfg: AppConfig, demo: bool = False) -> tuple[Collector, Storage]:
    storage = Storage(cfg.collector.db_path)
    if demo:                                                   # parc fictif : ni net-snmp ni réseau
        client = DemoClient()
        return Collector(storage, client, cfg.thresholds, make_notifier(cfg.smtp), cfg.collector, hp_fetch=client.ledm), storage
    return Collector(storage, NetSnmpClient(cfg.snmp), cfg.thresholds, make_notifier(cfg.smtp), cfg.collector), storage


def import_printers(storage: Storage, printers: list[Printer], overwrite: bool = False) -> tuple[int, int]:
    """Importe une liste (CSV) dans la base. Par défaut n'ajoute que les imprimantes inconnues ; `overwrite` remplace aussi les fiches existantes."""
    added = updated = 0
    for p in printers:
        cur = storage.get_printer(p.id)
        if cur is None:
            storage.upsert_printer(p)
            added += 1
        elif overwrite and cur != p:
            storage.upsert_printer(p)
            updated += 1
    return added, updated


def _printers_for_cycle(storage: Storage, csv_path: str | None) -> list[Printer]:
    """La base fait foi (liste éditée depuis le dashboard). Le CSV n'amorce qu'une base VIDE ; ensuite : `printwatch import`."""
    if storage.count_printers() == 0:
        if not (csv_path and Path(csv_path).is_file()):
            raise ConfigError("aucune imprimante : ajoutez-en depuis le dashboard ou importez un CSV (printwatch import -p printers.csv)")
        added, _ = import_printers(storage, load_printers(csv_path))
        log.info("base vide : %d imprimante(s) importée(s) depuis %s", added, csv_path)
    return [p for p in storage.list_printers() if p.enabled]


def cmd_once(args: argparse.Namespace, cfg: AppConfig) -> int:
    collector, storage = _build_collector(cfg)
    try:
        r = collector.run_cycle(_printers_for_cycle(storage, args.printers))
    finally:
        storage.close()
    print(f"{r.polled} interrogée(s), {r.failed} en échec, {r.events} événement(s), {r.notified} notifié(s) "
          f"en {r.duration_s:.1f} s")
    return 1 if r.failed and r.failed == r.polled else 0


def cmd_import(args: argparse.Namespace, cfg: AppConfig) -> int:
    storage = Storage(cfg.collector.db_path)
    try:
        printers = load_printers(args.printers)
        added, updated = import_printers(storage, printers, overwrite=args.overwrite)
        hidden = storage.disable_missing({p.id for p in printers}) if args.prune else 0
    finally:
        storage.close()
    print(f"{added} ajoutée(s), {updated} mise(s) à jour, {hidden} retirée(s) de la supervision (historique conservé).")
    return 0


def cmd_export(args: argparse.Namespace, cfg: AppConfig) -> int:
    """Sauvegarde lisible de la liste (le dashboard la modifie : la base fait foi). --excel : séparateur « ; » + BOM pour Excel en français."""
    storage = Storage(cfg.collector.db_path)
    try:
        printers = storage.list_printers()
    finally:
        storage.close()
    out = open(args.out, "w", newline="", encoding="utf-8-sig" if args.excel else "utf-8") if args.out else sys.stdout  # noqa: SIM115
    try:
        w = csv.writer(out, delimiter=";" if args.excel else ",", lineterminator="\n")
        w.writerow(["id", "name", "host", "brand", "model", "location", "user", "enabled"])
        for p in printers:
            w.writerow([p.id, p.name, p.host, p.brand, p.model, p.location, p.user, int(p.enabled)])
    finally:
        if args.out:
            out.close()
    print(f"{len(printers)} imprimante(s) exportée(s).", file=sys.stderr)
    return 0


def run_loop(collector: Collector, printers_path: str | None, interval_s: float, stop: threading.Event,
             ctl: SyncController | None = None) -> None:
    """Un cycle immédiat puis un toutes les `interval_s` (ou dès qu'une synchronisation est demandée). Une erreur est journalisée
    et le cycle suivant est tenté : la collecte ne s'arrête jamais en silence.

    interval_s = 0 (avec `ctl`) : mode manuel, aucun cycle au démarrage ni périodique ; uniquement sur demande de synchronisation."""
    manual = interval_s == 0 and ctl is not None
    if manual:
        ctl.wait(float("inf"), stop)                                           # type: ignore[union-attr]
    while not stop.is_set():
        try:
            printers = _printers_for_cycle(collector.storage, printers_path)      # relue à chaque cycle : édition à chaud depuis le dashboard
            with ctl.cycle() if ctl else nullcontext():
                r = collector.run_cycle(printers)
            log.info("cycle : %d interrogée(s), %d en échec, %d événement(s), %d notifié(s) (%.1f s)",
                     r.polled, r.failed, r.events, r.notified, r.duration_s)
        except ConfigError as exc:
            log.error("liste d'imprimantes inutilisable : %s", exc)
        except Exception:
            log.exception("cycle de collecte en échec ; nouvelle tentative %s", "à la prochaine synchronisation" if manual else f"dans {interval_s} s")
        ctl.wait(float("inf") if manual else interval_s, stop) if ctl else stop.wait(interval_s)


def cmd_loop(args: argparse.Namespace, cfg: AppConfig) -> int:
    if cfg.collector.interval_s == 0:
        raise ConfigError("collector.interval_s = 0 (synchronisation manuelle) ne vaut que pour `serve --collect` ; `loop` exige un intervalle")
    collector, storage = _build_collector(cfg)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    log.info("démarrage, intervalle %s s", cfg.collector.interval_s)
    try:
        run_loop(collector, args.printers, cfg.collector.interval_s, stop)
    finally:
        storage.close()
    return 0


def _forecast_days(storage: Storage, printer_id: str, key: str, now: float) -> tuple[float, str] | None:
    est = estimate(storage.history(printer_id, key, now - 35 * DAY))
    return (est.days_left, est.confidence) if est else None


def cmd_status(args: argparse.Namespace, cfg: AppConfig) -> int:
    storage = Storage(cfg.collector.db_path)
    now = time.time()
    try:
        rows = []
        for r in storage.overview():
            if r["key"] is None:
                rows.append({"printer": r["name"], "location": r["location"], "supply": "(aucun relevé)",
                             "pct": None, "alert": None, "status": "no_data", "down": bool(r["down"]),
                             "forecast_days": None, "confidence": None, "error": r["last_error"]})
                continue
            fc = None if r["down"] or r["pct"] is None else _forecast_days(storage, r["printer_id"], r["key"], now)   # injoignable : données périmées
            rows.append({"printer": r["name"], "location": r["location"], "supply": r["description"],
                         "kind": r["kind"], "pct": r["pct"], "alert": ("OK", "WARN", "CRIT")[r["alert_level"]],
                         "status": r["status"], "down": bool(r["down"]),
                         "forecast_days": None if fc is None else round(fc[0], 1),
                         "confidence": None if fc is None else fc[1], "error": r["last_error"]})
    finally:
        storage.close()
    rows.sort(key=lambda x: (x["pct"] is None, x["pct"] if x["pct"] is not None else 0))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    table = []
    for x in rows:
        state = "INJOIGNABLE" if x["down"] else (x["alert"] or "—")
        if x["pct"] is None and not x["down"]:
            state = x["status"]
        fc = "" if x["forecast_days"] is None else f"≈ {x['forecast_days']:.0f} j ({x['confidence']})"
        table.append([x["printer"], x["location"], x["supply"], _fmt_pct(x["pct"]), state, fc])
    print(_table(table, ["imprimante", "lieu", "consommable", "restant", "état", "rupture estimée"]))
    return 0


def _collector_thread(cfg: AppConfig, printers_path: str, stop: threading.Event, ctl: SyncController, demo: bool = False) -> threading.Thread:
    """Collecte intégrée au serveur web (mode PoC / poste unique) : une seule commande suffit pour avoir des données réelles."""
    Storage(cfg.collector.db_path).close()                # crée la base et le schéma avant la première requête web

    def work() -> None:
        collector, storage = _build_collector(cfg, demo)    # la connexion SQLite appartient au thread qui l'utilise
        try:
            run_loop(collector, printers_path, cfg.collector.interval_s, stop, ctl)
        finally:
            storage.close()

    t = threading.Thread(target=work, name="printwatch-collector", daemon=True)
    t.start()
    log.info("collecte intégrée active : %s (%s)", f"un cycle toutes les {cfg.collector.interval_s} s" if cfg.collector.interval_s
             else "synchronisation manuelle uniquement (interval_s = 0)", printers_path)
    return t


def _prepare_demo(cfg: AppConfig) -> None:
    """Base de démonstration jetable (repartie de zéro à chaque lancement), cycle d'une minute, parc fictif."""
    db = Path("demo.db")
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db}{suffix}").unlink(missing_ok=True)
    cfg.collector.db_path, cfg.collector.interval_s, cfg.collector.retry_delay_s = str(db), 60, 0
    storage = Storage(cfg.collector.db_path)
    import_printers(storage, [p for p, _ in FLEET])
    storage.close()
    print("MODE DÉMONSTRATION : parc fictif, relevés simulés, base jetable « demo.db ». Aucune imprimante n'est interrogée.")


def _writable(path: str) -> bool:
    p = Path(path).expanduser()
    return os.access(p if p.exists() else p.parent, os.W_OK)


def cmd_serve(args: argparse.Namespace, cfg: AppConfig) -> int:
    try:
        import uvicorn

        from .web import create_app
    except ImportError:
        print('Dépendances web absentes : pip install -e ".[web]"', file=sys.stderr)
        return 2
    stop = threading.Event()
    if args.demo:
        _prepare_demo(cfg)
    ctl = SyncController() if (args.collect or args.demo) else None
    if ctl:
        _collector_thread(cfg, args.printers, stop, ctl, demo=args.demo)
    # Gestion des imprimantes (ajout / édition / retrait). Avec des comptes (cas normal) : selon le rôle du compte connecté.
    # Sans comptes (démo) : libre en local, sinon uniquement avec un code administrateur (variable d'environnement, jamais en ligne de commande).
    loopback = args.host in ("127.0.0.1", "localhost", "::1")
    token = os.environ.get("PRINTWATCH_ADMIN_TOKEN") or None
    if not args.demo:
        if token:
            log.info("PRINTWATCH_ADMIN_TOKEN ignoré : les droits viennent du rôle de chaque compte")
        token = None
        manage = "off" if args.no_manage else "open"
    else:
        manage = "off" if args.no_manage else "token" if token else "open" if loopback else "off"
    if manage != "off" and not _writable(cfg.collector.db_path):
        log.warning("base en lecture seule : gestion des imprimantes désactivée")
        manage = "off"
    if args.demo and not loopback and manage == "off" and not args.no_manage:
        log.warning("écoute hors boucle locale : gestion des imprimantes désactivée (définir PRINTWATCH_ADMIN_TOKEN pour l'activer)")
    # En local, seuls les noms de la boucle locale sont acceptés dans l'en-tête Host (anti « rebinding DNS »). Derrière un proxy inverse,
    # déclarer le nom public : PRINTWATCH_ALLOWED_HOSTS="printwatch.exemple.sn,autre.nom". Exposé (hors boucle locale) : pas de contrôle.
    extra = [h.strip() for h in os.environ.get("PRINTWATCH_ALLOWED_HOSTS", "").split(",") if h.strip()]
    allowed = ["localhost", "127.0.0.1", "[::1]", *extra] if loopback else (extra or None)
    # Connexion obligatoire (sauf démo : parc fictif). Sans compte, personne n'entre : créer les comptes avec `printwatch users add`.
    users = None
    if not args.demo:
        users = UserStore(users_path(cfg))
        if not users.list():
            log.error("aucun compte dans %s : créer un compte avec `printwatch users add <adresse>`", users.path)
    app = create_app(cfg.collector.db_path, replacement_jump=cfg.collector.replacement_jump,
                     warn=cfg.thresholds.warn, crit=cfg.thresholds.crit, interval_s=cfg.collector.interval_s,
                     sync=ctl, manage=manage, admin_token=token, allowed_hosts=allowed, users=users,
                     glpi=cfg.glpi if cfg.glpi.est_configure() else None)
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    finally:
        stop.set()
    return 0


def users_path(cfg: AppConfig) -> Path:
    """Fichier des comptes : PRINTWATCH_USERS_FILE, sinon users.json à côté de la base (dossier data/, non versionné)."""
    return Path(os.environ.get("PRINTWATCH_USERS_FILE") or Path(cfg.collector.db_path).expanduser().parent / "users.json")


def _cli_actor() -> str:
    try:
        return "cli:" + getpass.getuser()
    except Exception:                                        # pas de nom de compte système (conteneur, service)
        return "cli"


def _cli_audit(cfg: AppConfig, action: str, target: str) -> None:
    """Les commandes de comptes laissent la même trace que l'interface (journal d'audit de la base)."""
    try:
        st = Storage(cfg.collector.db_path)
    except Exception as exc:
        log.warning("journal d'audit indisponible : %s", exc)
        return
    try:
        st.audit(time.time(), _cli_actor(), action, target, "cli")
    finally:
        st.close()


def cmd_users(args: argparse.Namespace, cfg: AppConfig) -> int:
    """Comptes du tableau de bord. Le service relit le fichier à chaque changement : aucun redémarrage nécessaire."""
    store = UserStore(users_path(cfg))
    try:
        if args.action == "list":
            rows = [[u["email"], u["role"], "oui" if u["must_change"] else "non",
                     datetime.fromtimestamp(u["updated_at"]).strftime("%d/%m/%Y %H:%M") if u["updated_at"] else ""] for u in store.list()]
            print(_table(rows, ["compte", "rôle", "mot de passe provisoire", "modifié le"]))
            print(f"\n{store.path}")
            return 0
        if args.action == "role":
            for email in args.emails:
                before = store.role(email)
                store.set_role(email, args.role)
                _cli_audit(cfg, "user_role", f"{normalize_email(email)} : {before} → {args.role}")
                print(f"{normalize_email(email)} : {args.role}")
            return 0
        for email in args.emails:
            if args.action == "remove":
                store.remove(email)
                _cli_audit(cfg, "user_remove", normalize_email(email))
                print(f"retiré : {normalize_email(email)}")
                continue
            if args.temp:                                   # provisoire ALÉATOIRE, affiché une seule fois, à changer à la connexion
                password, policy = temporary_password(), True
            else:
                password = getpass.getpass(f"Mot de passe pour {normalize_email(email)} : ")
                if password != getpass.getpass("Confirmer : "):
                    print("Les deux saisies diffèrent.", file=sys.stderr)
                    return 2
                policy = True
            if args.action == "add":
                store.add(email, password, must_change=args.temp, policy=policy, role=args.role)
                _cli_audit(cfg, "user_add", f"{normalize_email(email)} ({args.role})")
                print(f"créé : {normalize_email(email)} ({args.role})"
                      + (f" — mot de passe provisoire : {password}  (à transmettre de vive voix, à changer à la connexion)" if args.temp else ""))
            else:
                store.set_password(email, password, must_change=args.temp, policy=policy)
                _cli_audit(cfg, "user_reset" if args.temp else "user_passwd", normalize_email(email))
                print(f"mot de passe changé : {normalize_email(email)}"
                      + (f" — mot de passe provisoire : {password}  (à transmettre de vive voix)" if args.temp else ""))
    except AuthError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_secret(args: argparse.Namespace, cfg: AppConfig) -> int:
    """Le coffre DPAPI : poser, retirer, lister.

    LA VALEUR NE SE PASSE PAS EN ARGUMENT, elle se saisit. Un mot de passe tapé sur la
    ligne de commande reste dans l'historique de PowerShell (`ConsoleHost_history.txt`),
    lisible de n'importe qui ouvrant la session — ce qui annulerait l'intérêt du coffre.

    LES VALEURS NE S'AFFICHENT JAMAIS, même sur demande : `list` rend les NOMS. « Le
    secret est-il posé ? » est la seule question qu'on a besoin de poser depuis un
    terminal, et c'est celle à laquelle on répond.
    """
    from . import secrets_vault

    if args.action == "list":
        try:
            noms = secrets_vault.list_secret_names()
        except Exception as exc:                        # coffre illisible : on le DIT
            print(f"Coffre illisible : {exc}", file=sys.stderr)
            print()
            print(secrets_vault.vault_path())
            return 2
        print(_table([[n] for n in noms] or [["(aucun)"]], ["secret posé"]))
        print()
        print(secrets_vault.vault_path())
        return 0

    if args.action == "remove":
        for nom in args.names:
            print(f"{nom} : " + ("retiré" if secrets_vault.delete_secret(nom) else "absent"))
        return 0

    for nom in args.names:
        valeur = getpass.getpass(f"Valeur de {nom} (invisible) : ")
        if not valeur:
            print("Valeur vide : rien n'a été posé.", file=sys.stderr)
            return 2
        if valeur != getpass.getpass("Confirmer : "):
            print("Les deux saisies diffèrent.", file=sys.stderr)
            return 2
        secrets_vault.set_secret(nom, valeur)
        print(f"{nom} : posé")
    print()
    print(secrets_vault.vault_path())
    return 0


def cmd_glpi(args: argparse.Namespace, cfg: AppConfig) -> int:
    """Le lien vers GLPI : ce qui est réglé, ce qui manque, et un appel réel.

    ELLE NE CRÉE RIEN ET NE MODIFIE RIEN. C'est la commande à lancer quand l'onglet
    « Répartir les tickets » reste vide : elle distingue les trois pannes qui se
    ressemblent à l'écran — réglages absents, secrets absents, serveur qui refuse.
    """
    from .glpi import ErreurGlpi, Glpi

    g = cfg.glpi
    rows = [["url", g.url or "(vide)"],
            ["client_id", (g.client_id[:12] + "…") if g.client_id else "(vide)"],
            ["username", g.username or "(vide)"],
            ["entité", str(g.entity_id)]]
    # ON NE LAISSE PAS L'ABSENCE LEVER ICI : c'est le cas que la commande existe pour
    # nommer. `secrets()` refuse quand un secret manque -- juste a l'usage, fatal dans un
    # diagnostic, ou l'exception ferait disparaitre le tableau lui-meme.
    try:
        secrets = g.secrets()
    except ConfigError:
        secrets = {}
    for nom, cle in (("client_secret", g.client_secret_key),
                     ("password", g.password_key)):
        rows.append([cle, "posé" if secrets.get(nom) else "ABSENT"])
    print(_table(rows, ["réglage", "valeur"]))

    client = Glpi(g)
    if not client.est_actif():
        manquants = [cle for nom, cle in (("client_secret", g.client_secret_key),
                                          ("password", g.password_key))
                     if not secrets.get(nom)]
        print()
        print("Le lien est inactif : " + (
            "la section [glpi] du fichier de configuration est incomplète."
            if not g.est_configure()
            else "il manque dans le coffre " + ", ".join(manquants) + "."), file=sys.stderr)
        return 2

    try:
        tickets = client.lister_tickets(limite=args.limit)
    except ErreurGlpi as exc:
        print()
        print(f"GLPI a refusé : {exc}", file=sys.stderr)
        return 2

    print()
    print(f"{len(tickets)} ticket(s) ouvert(s), dont "
          f"{sum(1 for t in tickets if t.get('depuis_intranet'))} déposé(s) depuis l'intranet.")
    if tickets:
        print(_table([[str(t["id"]), (t["objet"] or "")[:52], t["statut_nom"],
                       "intranet" if t.get("depuis_intranet") else ""] for t in tickets[:10]],
                     ["n°", "objet", "état", "origine"]))
    return 0


def cmd_audit(args: argparse.Namespace, cfg: AppConfig) -> int:
    """Journal d'audit : connexions, comptes, paramètres, imprimantes, incidents, tickets (jamais de mot de passe)."""
    storage = Storage(cfg.collector.db_path)
    try:
        rows = storage.recent_audit(args.limit, user=args.user, action=args.action)
    finally:
        storage.close()
    print(_table([[datetime.fromtimestamp(r["ts"]).strftime("%d/%m %H:%M:%S"), r["user"], r["action"], r["target"][:70], r["source"]]
                  for r in rows], ["date", "compte", "action", "détail", "poste"]))
    return 0


def cmd_events(args: argparse.Namespace, cfg: AppConfig) -> int:
    storage = Storage(cfg.collector.db_path)
    try:
        rows = [[datetime.fromtimestamp(r["ts"]).strftime("%d/%m %H:%M"), r["type"], r["printer_name"],
                 r["message"], "oui" if r["notified"] else "non"] for r in storage.recent_events(args.limit)]
    finally:
        storage.close()
    print(_table(rows, ["date", "type", "imprimante", "message", "envoyé"]))
    return 0


# --- point d'entrée ----------------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="printwatch", description=__doc__)
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("-c", "--config", help="fichier TOML (défaut : config.toml s'il existe)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("probe", help="lire les consommables d'UNE imprimante (aucune écriture)")
    p.add_argument("host")
    p.set_defaults(fn=cmd_probe)

    for name, fn, helptext in (
        ("matrix", cmd_matrix, "matrice de compatibilité par marque/modèle (sortie CSV sans adresses IP)"),
        ("once", cmd_once, "un cycle de collecte puis arrêt"),
        ("loop", cmd_loop, "collecte en continu"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("-p", "--printers", default=None if name == "matrix" else "printers.csv",
                       help="CSV (matrix : sans -p, la liste de la base ; once/loop : amorçage d'une base vide)")
        if name == "matrix":
            p.add_argument("-o", "--out", help="fichier CSV de sortie (défaut : stdout)")
        p.set_defaults(fn=fn)

    p = sub.add_parser("status", help="état courant, trié par urgence")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("serve", help="tableau de bord web (écoute en local par défaut)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--collect", action="store_true", help="lance aussi la collecte SNMP en arrière-plan (sans second processus)")
    p.add_argument("-p", "--printers", default="printers.csv", help="CSV d'amorçage utilisé avec --collect si la base est vide")
    p.add_argument("--demo", action="store_true", help="parc fictif et relevés simulés (aucune imprimante ni net-snmp requis), base jetable demo.db")
    p.add_argument("--no-manage", action="store_true", help="interdit l'ajout / l'édition / le retrait d'imprimantes depuis le dashboard")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("import", help="importer un CSV d'imprimantes dans la base (n'ajoute que les nouvelles par défaut)")
    p.add_argument("-p", "--printers", default="printers.csv")
    p.add_argument("--overwrite", action="store_true", help="remplace aussi les fiches existantes par celles du CSV")
    p.add_argument("--prune", action="store_true", help="retire de la supervision les imprimantes absentes du CSV (historique conservé)")
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("export", help="exporter la liste d'imprimantes (sauvegarde ; --excel pour Excel en français)")
    p.add_argument("-o", "--out", help="fichier de sortie (défaut : stdout)")
    p.add_argument("--excel", action="store_true")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("users", help="comptes autorisés sur le tableau de bord (liste, ajout, retrait, mot de passe)")
    usub = p.add_subparsers(dest="action", required=True)
    usub.add_parser("list", help="lister les comptes")
    for action, helptext in (("add", "créer des comptes"), ("passwd", "changer le mot de passe de comptes"), ("remove", "retirer des comptes")):
        q = usub.add_parser(action, help=helptext)
        q.add_argument("emails", nargs="+", metavar="adresse")
        if action != "remove":
            q.add_argument("--temp", action="store_true",
                           help="mot de passe provisoire ALÉATOIRE, affiché une fois (à changer à la première connexion)")
        if action == "add":
            q.add_argument("--role", choices=ROLES, default="ecriture", help="droits du compte (défaut : ecriture)")
    q = usub.add_parser("role", help="changer les droits de comptes : lecture, ecriture ou admin")
    q.add_argument("role", choices=ROLES)
    q.add_argument("emails", nargs="+", metavar="adresse")
    p.set_defaults(fn=cmd_users)

    p = sub.add_parser("secret", help="coffre DPAPI : poser, retirer, lister les secrets (les valeurs ne s'affichent jamais)")
    ssub = p.add_subparsers(dest="action", required=True)
    ssub.add_parser("list", help="lister les NOMS des secrets posés")
    for action, helptext in (("set", "poser un secret (la valeur se saisit, elle ne se passe pas en argument)"),
                             ("remove", "retirer un secret")):
        q = ssub.add_parser(action, help=helptext)
        q.add_argument("names", nargs="+", metavar="NOM")
    p.set_defaults(fn=cmd_secret)

    p = sub.add_parser("glpi", help="vérifier le lien GLPI : réglages, secrets, et un appel réel (aucune écriture)")
    p.add_argument("-n", "--limit", type=int, default=60, help="nombre de tickets (1 à 200)")
    p.set_defaults(fn=cmd_glpi)

    p = sub.add_parser("audit", help="journal d'audit (qui a fait quoi) : connexions, comptes, paramètres, imprimantes, tickets")
    p.add_argument("-n", "--limit", type=int, default=50)
    p.add_argument("--user", help="seulement ce compte")
    p.add_argument("--action", help="seulement ces actions (préfixe : login, user, printer, ticket, settings…)")
    p.set_defaults(fn=cmd_audit)

    p = sub.add_parser("events", help="derniers événements")
    p.add_argument("-n", "--limit", type=int, default=30)
    p.set_defaults(fn=cmd_events)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        if getattr(args, "demo", False):                  # démo : configuration neutre (aucun fichier lu : ni SMTP réel ni base de production)
            cfg = AppConfig()
            cfg.snmp.version = "2c"
            return args.fn(args, cfg)
        cfg_path = args.config or ("config.toml" if Path("config.toml").is_file() else None)
        return args.fn(args, load_config(cfg_path))
    except ConfigError as exc:
        print(f"Configuration : {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
