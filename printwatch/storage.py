"""Persistance SQLite : état courant, historique des relevés, journal d'événements."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Collection, Iterable

from .alerts import AlertSettings
from .model import EventType, Printer, Supply

SCHEMA = """
CREATE TABLE IF NOT EXISTS printers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    host TEXT NOT NULL,
    brand TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    user TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    sys_descr TEXT,
    last_seen REAL,
    last_error TEXT,
    failures INTEGER NOT NULL DEFAULT 0,
    down INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS supplies (
    printer_id TEXT NOT NULL REFERENCES printers(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    description TEXT NOT NULL,
    kind TEXT NOT NULL,
    color TEXT,
    status TEXT NOT NULL,
    pct REAL,
    level INTEGER,
    max_capacity INTEGER,
    alert_level INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    fc_days REAL,                                  -- rupture estimée (jours), calculée une fois par cycle par le collecteur
    fc_conf TEXT,                                  -- confiance : low | medium | high
    PRIMARY KEY (printer_id, key)
);
CREATE TABLE IF NOT EXISTS samples (
    printer_id TEXT NOT NULL,
    key TEXT NOT NULL,
    ts REAL NOT NULL,
    pct REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_samples ON samples (printer_id, key, ts);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    printer_id TEXT NOT NULL,
    key TEXT,
    type TEXT NOT NULL,
    pct REAL,
    message TEXT NOT NULL,
    notified INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_pending ON events (notified, id);
CREATE TABLE IF NOT EXISTS device_info (     -- fiche matérielle du dernier relevé (JSON produit par device.parse_device)
    printer_id TEXT PRIMARY KEY REFERENCES printers(id) ON DELETE CASCADE,
    data TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS counters (        -- historique du compteur de pages (volume imprimé par période)
    printer_id TEXT NOT NULL,
    ts REAL NOT NULL,
    pages INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_counters ON counters (printer_id, ts);
CREATE TABLE IF NOT EXISTS cycles (          -- battement de cœur : un enregistrement par cycle de collecte terminé
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    polled INTEGER NOT NULL,
    failed INTEGER NOT NULL,
    events INTEGER NOT NULL DEFAULT 0,
    duration REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS incidents (       -- incidents saisis à la main sur une imprimante (historique conservé)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    printer_id TEXT NOT NULL REFERENCES printers(id),
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    opened_at REAL NOT NULL,
    opened_by TEXT NOT NULL,
    resolved_at REAL,
    resolved_by TEXT,
    resolution TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents ON incidents (printer_id, opened_at);
CREATE TABLE IF NOT EXISTS technicians (     -- qui peut recevoir un ticket d'assistance
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    -- FACULTATIF, ET C'EST TOUTE LA SUBTILITÉ. L'API de GLPI exige un identifiant
    -- d'utilisateur pour désigner un acteur : sans compte GLPI, l'attribution ne peut pas
    -- y être écrite. Elle devient alors un suivi dans la chronologie du ticket, et
    -- l'attributaire affiché vient de `ticket_assignments`, marqué « hors GLPI ».
    -- Le cas s'est présenté le 06/10/2026 avec un stagiaire de passage, et il ne se
    -- contourne pas.
    glpi_email TEXT,
    -- PAS DE DATE DE FIN, un simple interrupteur : un carnet qui se vide tout seul à une
    -- date que personne ne relit se constate trop tard.
    active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    created_by TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_technicians_name ON technicians (name);
CREATE TABLE IF NOT EXISTS ticket_assignments (   -- ce que PrintWatch a confié, et à qui
    -- UNE LIGNE PAR TICKET : confier de nouveau remplace, il n'y a qu'un attributaire.
    ticket_id INTEGER PRIMARY KEY,
    technician_id INTEGER NOT NULL REFERENCES technicians(id),
    -- Vrai quand l'attribution a pu être écrite dans GLPI. Faux : la ligne ci-dessous est
    -- alors la SEULE réponse à « qui tient ce ticket ».
    in_glpi INTEGER NOT NULL DEFAULT 0,
    assigned_at REAL NOT NULL,
    assigned_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (           -- journal des actions : qui, quoi, sur quoi, d'où. JAMAIS de mot de passe ni de jeton
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    user TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit (ts);
CREATE TABLE IF NOT EXISTS settings (        -- réglages modifiés depuis le dashboard (JSON), prioritaires sur config.toml
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Storage:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)   # data/ n'existe pas au premier lancement
            path = str(Path(path).expanduser())
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        # Migrations additives : bases créées avant l'ajout de ces colonnes.
        self._ensure_column("printers", "user", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("supplies", "fc_days", "REAL")
        self._ensure_column("supplies", "fc_conf", "TEXT")

    def _columns(self, table: str) -> set[str]:
        return {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}      # noqa: S608 : nom de table interne

    def _printer_columns(self) -> set[str]:
        return self._columns("printers")

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        if column not in self._columns(table):
            with self.conn:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")   # noqa: S608 : valeurs internes constantes

    def close(self) -> None:
        self.conn.close()

    # --- réglages ----------------------------------------------------------------------------------------------
    def alert_settings(self, default: AlertSettings) -> AlertSettings:
        """Seuils et consommables suivis enregistrés depuis le dashboard ; `default` (config.toml) tant que rien n'a été réglé,
        ou si la base précède la table des réglages (connexion en lecture seule, pas encore migrée)."""
        try:
            row = self.conn.execute("SELECT value FROM settings WHERE key='alerts'").fetchone()
        except sqlite3.OperationalError:
            return default
        if row is None:
            return default
        try:
            return AlertSettings.from_dict(json.loads(row[0]), default)
        except (ValueError, TypeError):
            return default

    # --- incidents saisis -------------------------------------------------------------------------------------------
    _INCIDENT_COLS = "id, printer_id, category, description, opened_at, opened_by, resolved_at, resolved_by, resolution"

    def add_incident(self, printer_id: str, category: str, description: str, ts: float, author: str) -> int:
        with self.conn:
            cur = self.conn.execute("INSERT INTO incidents (printer_id, category, description, opened_at, opened_by) VALUES (?, ?, ?, ?, ?)",
                                    (printer_id, category, description, ts, author))
        return int(cur.lastrowid)

    def get_incident(self, incident_id: int) -> dict | None:
        row = self.conn.execute(f"SELECT {self._INCIDENT_COLS} FROM incidents WHERE id=?", (incident_id,)).fetchone()   # noqa: S608
        return dict(row) if row else None

    def resolve_incident(self, incident_id: int, ts: float, author: str, resolution: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE incidents SET resolved_at=?, resolved_by=?, resolution=? WHERE id=? AND resolved_at IS NULL",
                              (ts, author, resolution, incident_id))

    def incidents(self, printer_id: str, limit: int = 200) -> list[dict]:
        """Historique d'une imprimante : incidents ouverts d'abord, puis du plus récent au plus ancien."""
        rows = self.conn.execute(f"SELECT {self._INCIDENT_COLS} FROM incidents WHERE printer_id=? "      # noqa: S608
                                 "ORDER BY resolved_at IS NOT NULL, opened_at DESC LIMIT ?", (printer_id, limit))
        return [dict(r) for r in rows]

    def open_incidents(self) -> dict[str, list[dict]]:
        """Incidents ouverts, par imprimante (lecture seule tolérée sur une base pas encore migrée)."""
        try:
            rows = self.conn.execute(f"SELECT {self._INCIDENT_COLS} FROM incidents WHERE resolved_at IS NULL ORDER BY opened_at")   # noqa: S608
        except sqlite3.OperationalError:
            return {}
        out: dict[str, list[dict]] = {}
        for r in rows:
            out.setdefault(r["printer_id"], []).append(dict(r))
        return out

    # ── Répartition des tickets d'assistance ────────────────────────────────────
    #
    # LE CARNET EST TENU À LA MAIN, il n'est pas déduit de GLPI. Il faut pouvoir y
    # inscrire quelqu'un QUI N'A PAS DE COMPTE GLPI — un stagiaire, un prestataire de
    # passage — et l'API de GLPI ne connaît que ses propres comptes.

    _TECH_COLS = "id, name, glpi_email, active, created_at, created_by"

    def technicians(self, actifs_seulement: bool = False) -> list[dict]:
        """Le carnet, par ordre alphabétique.

        LES INACTIFS SONT SERVIS AUSSI, sauf demande contraire : un technicien retiré
        reste l'attributaire de ses anciens tickets, et la colonne « confié à » doit
        pouvoir le nommer. Le masquer ferait disparaître un nom sans rien expliquer.
        """
        where = " WHERE active=1" if actifs_seulement else ""
        rows = self.conn.execute(
            f"SELECT {self._TECH_COLS} FROM technicians{where} ORDER BY name COLLATE NOCASE")  # noqa: S608
        return [dict(r) for r in rows]

    def technician(self, technician_id: int) -> dict | None:
        row = self.conn.execute(
            f"SELECT {self._TECH_COLS} FROM technicians WHERE id=?",                 # noqa: S608
            (technician_id,)).fetchone()
        return dict(row) if row else None

    def add_technician(self, name: str, glpi_email: str | None, ts: float,
                       author: str) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO technicians (name, glpi_email, created_at, created_by) "
                "VALUES (?, ?, ?, ?)", (name, glpi_email or None, ts, author))
        return int(cur.lastrowid)

    def update_technician(self, technician_id: int, *, name: str | None = None,
                          glpi_email: str | None = None,
                          active: bool | None = None) -> None:
        """Ne touche QUE ce qui est passé.

        `glpi_email` a trois états, et les confondre coûterait cher : absent (on n'y
        touche pas), une adresse (on la pose), la chaîne vide (on l'efface, et le
        technicien repasse « hors GLPI »). D'où la sentinelle plutôt qu'un `or None`.
        """
        champs, valeurs = [], []
        if name is not None:
            champs.append("name=?")
            valeurs.append(name)
        if glpi_email is not None:
            champs.append("glpi_email=?")
            valeurs.append(glpi_email or None)
        if active is not None:
            champs.append("active=?")
            valeurs.append(1 if active else 0)
        if not champs:
            return
        valeurs.append(technician_id)
        with self.conn:
            self.conn.execute(
                f"UPDATE technicians SET {', '.join(champs)} WHERE id=?",            # noqa: S608
                valeurs)

    def assign_ticket(self, ticket_id: int, technician_id: int, in_glpi: bool,
                      ts: float, author: str) -> None:
        """Confier, ou re-confier. Un ticket n'a qu'un attributaire."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO ticket_assignments "
                "  (ticket_id, technician_id, in_glpi, assigned_at, assigned_by) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(ticket_id) DO UPDATE SET "
                "  technician_id=excluded.technician_id, in_glpi=excluded.in_glpi, "
                "  assigned_at=excluded.assigned_at, assigned_by=excluded.assigned_by",
                (int(ticket_id), int(technician_id), 1 if in_glpi else 0, ts, author))

    def assignments(self, ticket_ids=None) -> dict[int, dict]:
        """`{numéro: {nom, hors_glpi, …}}` pour les tickets demandés, ou pour tous.

        LA JOINTURE EST ICI et non à l'écran : ce qu'on veut afficher est un NOM, et aller
        le chercher ligne à ligne depuis le front ferait autant de requêtes que de lignes.
        """
        sql = ("SELECT a.ticket_id, a.technician_id, a.in_glpi, a.assigned_at, "
               "       a.assigned_by, t.name, t.glpi_email "
               "  FROM ticket_assignments a JOIN technicians t ON t.id = a.technician_id")
        params: tuple = ()
        if ticket_ids is not None:
            ids = [int(i) for i in ticket_ids if i]
            if not ids:
                return {}
            sql += f" WHERE a.ticket_id IN ({','.join('?' * len(ids))})"
            params = tuple(ids)
        out: dict[int, dict] = {}
        for r in self.conn.execute(sql, params):
            out[int(r["ticket_id"])] = {
                "technician_id": r["technician_id"],
                "name": r["name"],
                # « hors GLPI » se déduit, il ne se stocke pas deux fois : c'est
                # exactement « l'attribution n'a pas pu être écrite dans GLPI ».
                "hors_glpi": not bool(r["in_glpi"]),
                "assigned_at": r["assigned_at"],
                "assigned_by": r["assigned_by"],
            }
        return out

    # --- journal d'audit ------------------------------------------------------------------------------------------
    def audit(self, ts: float, user: str, action: str, target: str = "", source: str = "") -> None:
        with self.conn:
            self.conn.execute("INSERT INTO audit (ts, user, action, target, source) VALUES (?, ?, ?, ?, ?)",
                              (ts, user[:254], action[:40], target[:600], source[:64]))

    def recent_audit(self, limit: int = 100, user: str | None = None, action: str | None = None,
                     before_id: int | None = None, since: float | None = None) -> list[dict]:
        """Du plus récent au plus ancien. `action` est un PRÉFIXE (« user » trouve user_add, user_role…)."""
        where, args = [], []
        if user:
            where.append("user = ?"); args.append(user.strip().lower())
        if action:
            where.append("action LIKE ? ESCAPE '\\'"); args.append(action.strip().replace("\\", "").replace("%", "").replace("_", "\\_") + "%")
        if before_id:
            where.append("id < ?"); args.append(int(before_id))
        if since is not None:
            where.append("ts >= ?"); args.append(float(since))
        sql = "SELECT id, ts, user, action, target, source FROM audit"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        try:
            rows = self.conn.execute(sql, (*args, max(1, min(int(limit), 10000)))).fetchall()
        except sqlite3.OperationalError:                       # base antérieure au journal, ouverte en lecture seule
            return []
        return [dict(r) for r in rows]

    def set_alert_settings(self, s: AlertSettings) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO settings (key, value) VALUES ('alerts', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                              (json.dumps(s.as_dict()),))

    # --- imprimantes -----------------------------------------------------------------------------------------
    def upsert_printer(self, p: Printer) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO printers (id, name, host, brand, model, location, user, enabled)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name, host=excluded.host, brand=excluded.brand,
                       model=excluded.model, location=excluded.location, user=excluded.user,
                       enabled=excluded.enabled""",
                (p.id, p.name, p.host, p.brand, p.model, p.location, p.user, int(p.enabled)),
            )

    def ensure_printer(self, p: Printer) -> None:
        """Crée l'imprimante si elle est inconnue ; ne modifie JAMAIS une fiche existante (la base fait foi, éditée depuis le dashboard)."""
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO printers (id, name, host, brand, model, location, user, enabled) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (p.id, p.name, p.host, p.brand, p.model, p.location, p.user, int(p.enabled)),
            )

    @staticmethod
    def _printer(r: sqlite3.Row) -> Printer:
        return Printer(id=r["id"], name=r["name"], host=r["host"], brand=r["brand"], model=r["model"],
                       location=r["location"], user=r["user"], enabled=bool(r["enabled"]))

    def list_printers(self) -> list[Printer]:
        return [self._printer(r) for r in self.conn.execute(
            "SELECT id, name, host, brand, model, location, user, enabled FROM printers ORDER BY name")]

    def count_printers(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM printers").fetchone()[0])

    def get_printer(self, printer_id: str) -> Printer | None:
        r = self.conn.execute("SELECT id, name, host, brand, model, location, user, enabled FROM printers WHERE id=?", (printer_id,)).fetchone()
        return self._printer(r) if r else None

    def printer_by_host(self, host: str) -> Printer | None:
        """Fiche utilisant cette adresse (une imprimante active de préférence)."""
        r = self.conn.execute("SELECT id, name, host, brand, model, location, user, enabled FROM printers WHERE host=? ORDER BY enabled DESC LIMIT 1", (host,)).fetchone()
        return self._printer(r) if r else None

    EDITABLE = ("name", "host", "brand", "model", "location", "user")

    def update_printer(self, printer_id: str, **fields: str) -> bool:
        """Met à jour les champs éditables fournis. Un changement d'adresse remet à zéro l'état « injoignable »."""
        fields = {k: v for k, v in fields.items() if k in self.EDITABLE}
        if not fields:
            return self.get_printer(printer_id) is not None
        sets = ", ".join(f"{k}=?" for k in fields)
        reset = ", failures=0, down=0, last_error=NULL" if "host" in fields else ""
        with self.conn:
            cur = self.conn.execute(f"UPDATE printers SET {sets}{reset} WHERE id=?", [*fields.values(), printer_id])  # noqa: S608 : clés en liste blanche
        return cur.rowcount > 0

    def set_enabled(self, printer_id: str, enabled: bool) -> bool:
        """Retire (False) ou remet (True) une imprimante en supervision ; l'historique est conservé dans les deux cas."""
        with self.conn:
            cur = self.conn.execute("UPDATE printers SET enabled=? WHERE id=?", (int(enabled), printer_id))
        return cur.rowcount > 0

    def is_down(self, printer_id: str) -> bool:
        row = self.conn.execute("SELECT down FROM printers WHERE id=?", (printer_id,)).fetchone()
        return bool(row and row["down"])

    def record_success(self, printer_id: str, ts: float, sys_descr: str | None) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE printers SET last_seen=?, last_error=NULL, failures=0, down=0,
                   sys_descr=COALESCE(?, sys_descr) WHERE id=?""",
                (ts, sys_descr, printer_id),
            )

    def record_failure(self, printer_id: str, error: str) -> int:
        """Incrémente le compteur d'échecs consécutifs et le renvoie."""
        with self.conn:
            self.conn.execute(
                "UPDATE printers SET failures=failures+1, last_error=? WHERE id=?", (error[:300], printer_id)
            )
        return int(self.conn.execute("SELECT failures FROM printers WHERE id=?", (printer_id,)).fetchone()[0])

    def mark_down(self, printer_id: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE printers SET down=1 WHERE id=?", (printer_id,))

    # --- consommables ----------------------------------------------------------------------------------------
    def get_supply(self, printer_id: str, key: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM supplies WHERE printer_id=? AND key=?", (printer_id, key)
        ).fetchone()

    def upsert_supply(self, printer_id: str, s: Supply, alert_level: int, ts: float) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO supplies (printer_id, key, description, kind, color, status, pct, level,
                       max_capacity, alert_level, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(printer_id, key) DO UPDATE SET description=excluded.description, kind=excluded.kind,
                       color=excluded.color, status=excluded.status, pct=excluded.pct, level=excluded.level,
                       max_capacity=excluded.max_capacity, alert_level=excluded.alert_level,
                       updated_at=excluded.updated_at""",
                (printer_id, s.key, s.description, s.kind.value, s.color, s.status.value, s.pct_remaining,
                 s.level, s.max_capacity, alert_level, ts),
            )
            if s.pct_remaining is not None:
                self.conn.execute(
                    "INSERT INTO samples (printer_id, key, ts, pct) VALUES (?, ?, ?, ?)",
                    (printer_id, s.key, ts, s.pct_remaining),
                )

    def set_forecast(self, printer_id: str, key: str, days_left: float | None, confidence: str | None) -> None:
        """Mémorise la rupture estimée (None : pas de prévision possible). Calculée par le collecteur, lue telle quelle par l'API."""
        with self.conn:
            self.conn.execute("UPDATE supplies SET fc_days=?, fc_conf=? WHERE printer_id=? AND key=?",
                              (days_left, confidence, printer_id, key))

    def history(self, printer_id: str, key: str, since: float) -> list[tuple[float, float]]:
        rows = self.conn.execute(
            "SELECT ts, pct FROM samples WHERE printer_id=? AND key=? AND ts>=? ORDER BY ts",
            (printer_id, key, since),
        ).fetchall()
        return [(r["ts"], r["pct"]) for r in rows]

    def samples_since(self, since: float, printer_id: str | None = None) -> list[sqlite3.Row]:
        """Tous les relevés depuis `since` (une seule requête pour tout le parc), triés par série puis par date."""
        sql = "SELECT printer_id, key, ts, pct FROM samples WHERE ts>=?"
        args: list[Any] = [since]
        if printer_id is not None:
            sql += " AND printer_id=?"
            args.append(printer_id)
        return self.conn.execute(sql + " ORDER BY printer_id, key, ts", args).fetchall()

    # --- fiche matérielle et compteur de pages -------------------------------------------------------------
    def set_device_info(self, printer_id: str, info: dict[str, Any], ts: float) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO device_info (printer_id, data, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(printer_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at""",
                (printer_id, json.dumps(info, ensure_ascii=False), ts),
            )
            if isinstance(info.get("page_count"), int):
                self.conn.execute("INSERT INTO counters (printer_id, ts, pages) VALUES (?, ?, ?)",
                                  (printer_id, ts, info["page_count"]))

    def device_infos(self) -> dict[str, dict[str, Any]]:
        """{imprimante: fiche + `updated_at`} ; vide sur une base antérieure à la table (ouverte en lecture seule)."""
        try:
            rows = self.conn.execute("SELECT printer_id, data, updated_at FROM device_info").fetchall()
        except sqlite3.OperationalError:
            return {}
        return {r["printer_id"]: {**json.loads(r["data"]), "updated_at": r["updated_at"]} for r in rows}

    def pages_since(self, since: float) -> dict[str, int]:
        """Pages imprimées depuis `since` : dernier compteur moins le premier de la période. Un compteur qui recule
        (carte remplacée) rend la valeur incalculable : l'imprimante est alors omise."""
        try:
            rows = self.conn.execute("SELECT printer_id, pages FROM counters WHERE ts>=? ORDER BY printer_id, ts", (since,)).fetchall()
        except sqlite3.OperationalError:
            return {}
        first: dict[str, int] = {}
        last: dict[str, int] = {}
        for r in rows:
            first.setdefault(r["printer_id"], r["pages"])
            last[r["printer_id"]] = r["pages"]
        return {pid: last[pid] - first[pid] for pid in first if last[pid] >= first[pid]}

    def printers_basic(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT id, name, down, last_seen FROM printers WHERE enabled=1 ORDER BY name"
        ).fetchall()

    def availability_events(self) -> list[sqlite3.Row]:
        """Événements d'injoignabilité (`down`) et de retour (`up`) de toutes les imprimantes, dans l'ordre chronologique."""
        return self.conn.execute(
            "SELECT printer_id, ts, type FROM events WHERE type IN ('down','up') ORDER BY ts, id"
        ).fetchall()

    def disable_missing(self, keep_ids: Collection[str]) -> int:
        """Masque (enabled=0, historique conservé) les imprimantes absentes de la liste fournie. Réactivées si elles reviennent dans le CSV."""
        ids = list(keep_ids)
        if not ids:                                                # liste vide : on ne masque jamais tout le parc
            return 0
        marks = ",".join("?" * len(ids))
        with self.conn:
            cur = self.conn.execute(f"UPDATE printers SET enabled=0 WHERE enabled=1 AND id NOT IN ({marks})", ids)
        return cur.rowcount

    def record_cycle(self, ts: float, polled: int, failed: int, events: int, duration: float) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO cycles (ts, polled, failed, events, duration) VALUES (?, ?, ?, ?, ?)",
                              (ts, polled, failed, events, duration))

    def last_cycle(self) -> sqlite3.Row | None:
        try:
            return self.conn.execute("SELECT ts, polled, failed, events, duration FROM cycles ORDER BY id DESC LIMIT 1").fetchone()
        except sqlite3.OperationalError:                       # base d'avant la table `cycles`, ouverte en lecture seule
            return None

    def purge(self, before: float) -> int:
        with self.conn:
            self.conn.execute("DELETE FROM cycles WHERE ts<?", (before,))
            self.conn.execute("DELETE FROM counters WHERE ts<?", (before,))
            cur = self.conn.execute("DELETE FROM samples WHERE ts<?", (before,))
            self.conn.execute("DELETE FROM events WHERE ts<? AND notified=1", (before,))
            self.conn.execute("DELETE FROM audit WHERE ts<?", (before,))
        return cur.rowcount

    def overview(self) -> list[sqlite3.Row]:
        # Lecture seule sur une ancienne base : pas de migration possible, on complète par des valeurs vides.
        user = "p.user" if "user" in self._printer_columns() else "'' AS user"
        fc = "s.fc_days, s.fc_conf" if "fc_days" in self._columns("supplies") else "NULL AS fc_days, NULL AS fc_conf"
        return self.conn.execute(
            f"""SELECT p.id AS printer_id, p.name, p.host, p.brand, p.model, p.location, {user}, p.down, p.last_seen,
                      p.last_error, s.key, s.description, s.kind, s.color, s.status, s.pct, s.alert_level,
                      s.updated_at, {fc}
               FROM printers p LEFT JOIN supplies s ON s.printer_id = p.id
               WHERE p.enabled = 1
               ORDER BY p.name, s.key"""
        ).fetchall()

    # --- événements ------------------------------------------------------------------------------------------
    def add_event(
        self, ts: float, printer_id: str, key: str | None, etype: EventType, pct: float | None, message: str,
        *, notify: bool = True,
    ) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO events (ts, printer_id, key, type, pct, message, notified) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, printer_id, key, etype.value, pct, message, 0 if notify else 1),
            )
        return int(cur.lastrowid)

    def pending_events(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT e.id, e.ts, e.printer_id, e.key, e.type, e.pct, e.message,
                      p.name AS printer_name, p.host, p.location
               FROM events e JOIN printers p ON p.id = e.printer_id
               WHERE e.notified = 0 ORDER BY e.id"""
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_notified(self, ids: Iterable[int]) -> None:
        ids = list(ids)
        if not ids:
            return
        with self.conn:
            self.conn.executemany("UPDATE events SET notified=1 WHERE id=?", [(i,) for i in ids])

    def recent_events(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT e.id, e.ts, p.name AS printer_name, e.key, e.type, e.pct, e.message, e.notified
               FROM events e JOIN printers p ON p.id = e.printer_id ORDER BY e.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
