"""Configuration : fichier TOML pour les réglages, variables d'environnement pour les secrets, CSV pour le parc."""
from __future__ import annotations

import csv
import io
import dataclasses
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .alerts import Thresholds
from .model import Printer


class ConfigError(ValueError):
    """Configuration invalide ou incomplète."""


# Un hôte qui commencerait par « - » serait interprété comme une option par snmpwalk.
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-\[\]]*$")
_TRUE = {"1", "true", "yes", "oui", "y", "o", "vrai", "x"}      # « VRAI » : booléen d'Excel en français
_FALSE = {"0", "false", "no", "non", "n", "faux", ""}


@dataclass
class SnmpConfig:
    version: str = "3"                    # "1", "2c" ou "3"
    timeout: float = 3.0                  # secondes, par requête
    retries: int = 1
    command_timeout: float = 30.0         # durée maximale d'un snmpwalk complet
    community_env: str = "PRINTWATCH_SNMP_COMMUNITY"
    user: str = ""
    level: str = "authPriv"               # noAuthNoPriv | authNoPriv | authPriv
    auth_proto: str = "SHA"
    priv_proto: str = "AES"
    context: str = ""                     # nom de contexte SNMPv3 (-n) ; vide pour une imprimante, requis par snmpsim
    auth_pass_env: str = "PRINTWATCH_SNMP_AUTH_PASS"
    priv_pass_env: str = "PRINTWATCH_SNMP_PRIV_PASS"
    snmpwalk: str = "snmpwalk"
    snmpget: str = "snmpget"

    def validate(self) -> None:
        if self.version not in ("1", "2c", "3"):
            raise ConfigError("snmp.version doit valoir \"1\", \"2c\" ou \"3\"")
        if self.level not in ("noAuthNoPriv", "authNoPriv", "authPriv"):
            raise ConfigError("snmp.level invalide")
        if self.timeout <= 0 or self.retries < 0 or self.command_timeout <= 0:
            raise ConfigError("snmp.timeout / retries / command_timeout invalides")
        if self.version == "3" and not self.user:
            raise ConfigError("snmp.user est obligatoire en SNMPv3")

    def secrets(self) -> dict[str, str]:
        """Secrets lus dans l'environnement, jamais dans le fichier de configuration."""
        self.validate()
        out: dict[str, str] = {}

        def need(env: str) -> str:
            value = os.environ.get(env, "")
            if not value:
                raise ConfigError(f"variable d'environnement {env} absente ou vide")
            if any(c in value for c in '"\n\r'):
                raise ConfigError(f"{env} contient un caractère interdit (guillemet ou saut de ligne)")
            return value

        if self.version in ("1", "2c"):
            out["community"] = need(self.community_env)
        else:
            if self.level != "noAuthNoPriv":
                out["auth"] = need(self.auth_pass_env)
            if self.level == "authPriv":
                out["priv"] = need(self.priv_pass_env)
        return out


@dataclass
class SmtpConfig:
    host: str = ""
    port: int = 25
    starttls: bool = False
    user: str = ""
    password_env: str = "PRINTWATCH_SMTP_PASSWORD"
    sender: str = "printwatch@localhost"
    recipients: list[str] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.recipients)


@dataclass
class CollectorConfig:
    db_path: str = "printwatch.db"
    interval_s: int = 1800
    workers: int = 8
    down_after: int = 3                   # relevés consécutifs en échec avant l'alerte « injoignable »
    replacement_jump: float = 20.0        # hausse (points %) interprétée comme un remplacement
    retention_days: int = 400
    hp_ledm: bool = True                  # HP : combler les niveaux SNMP « inconnus » avec l'interface web (LEDM)
    retry_delay_s: float = 3.0            # relance une fois, dans le même cycle, les imprimantes en échec (0 = désactivé)

    def validate(self) -> None:
        if self.interval_s < 10 or self.workers < 1 or self.down_after < 1:
            raise ConfigError("collector.interval_s / workers / down_after invalides")
        if self.replacement_jump <= 0 or self.retention_days < 1:
            raise ConfigError("collector.replacement_jump / retention_days invalides")


@dataclass
class AppConfig:
    snmp: SnmpConfig = field(default_factory=SnmpConfig)
    thresholds: Thresholds = field(default_factory=Thresholds)
    smtp: SmtpConfig = field(default_factory=SmtpConfig)
    collector: CollectorConfig = field(default_factory=CollectorConfig)


def _build(cls: type, data: dict[str, Any], section: str):
    names = {f.name for f in dataclasses.fields(cls)}
    unknown = set(data) - names
    if unknown:
        raise ConfigError(f"[{section}] clé(s) inconnue(s) : {', '.join(sorted(unknown))}")
    try:
        return cls(**data)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"[{section}] {exc}") from exc


def load_config(path: str | Path | None) -> AppConfig:
    """Charge un fichier TOML ; sans fichier, renvoie les valeurs par défaut."""
    if path is None:
        cfg = AppConfig()
    else:
        p = Path(path)
        if not p.is_file():
            raise ConfigError(f"fichier de configuration introuvable : {p}")
        with p.open("rb") as fh:
            raw = tomllib.load(fh)
        unknown = set(raw) - {"snmp", "thresholds", "smtp", "collector"}
        if unknown:
            raise ConfigError(f"section(s) inconnue(s) : {', '.join(sorted(unknown))}")
        cfg = AppConfig(
            snmp=_build(SnmpConfig, raw.get("snmp", {}), "snmp"),
            thresholds=_build(Thresholds, raw.get("thresholds", {}), "thresholds"),
            smtp=_build(SmtpConfig, raw.get("smtp", {}), "smtp"),
            collector=_build(CollectorConfig, raw.get("collector", {}), "collector"),
        )
    cfg.snmp.validate()
    cfg.collector.validate()
    return cfg


def check_host(host: str) -> str:
    if not HOST_RE.match(host):
        raise ConfigError(f"adresse ou nom d'hôte invalide : {host!r}")
    return host


def load_printers(path: str | Path) -> list[Printer]:
    """CSV : id,name,host,brand,model,location,user,enabled (l'en-tête est obligatoire, seuls id et host sont requis ;
    `user` = utilisateur de l'imprimante, facultatif, la colonne `utilisateur` est acceptée comme alias)."""
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"liste d'imprimantes introuvable : {p}")
    printers: list[Printer] = []
    seen: set[str] = set()
    hosts: dict[str, str] = {}
    # Tolérant aux exports Excel : UTF-8 (avec ou sans BOM) sinon ANSI/cp1252 ; séparateur « , » ou « ; » (Excel en français) ou tabulation.
    raw = p.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    header = next((ln for ln in text.splitlines() if ln.strip()), "")
    delimiter = max(",;\t", key=header.count)
    with io.StringIO(text, newline="") as fh:
        reader = csv.DictReader(fh, delimiter=delimiter)
        reader.fieldnames = [(f or "").strip().lower() for f in reader.fieldnames or []]      # « ID », « Host »… acceptés
        if not {"id", "host"} <= set(reader.fieldnames):
            raise ConfigError("le CSV des imprimantes doit comporter au moins les colonnes id et host")
        for lineno, row in enumerate(reader, start=2):
            pid = (row.get("id") or "").strip()
            host = (row.get("host") or "").strip()
            if not pid and not host:
                continue
            if not pid or not host:
                raise ConfigError(f"{p.name} ligne {lineno} : id et host sont obligatoires")
            if pid in seen:
                raise ConfigError(f"{p.name} ligne {lineno} : id en double ({pid})")
            seen.add(pid)
            flag = (row.get("enabled") or "1").strip().lower()
            if flag not in _TRUE and flag not in _FALSE:             # jamais de désactivation silencieuse sur une faute de frappe
                raise ConfigError(f"{p.name} ligne {lineno} : valeur « {flag} » invalide pour enabled (1/0, oui/non, vrai/faux)")
            enabled = flag in _TRUE
            host = check_host(host)
            if host in hosts:
                raise ConfigError(f"{p.name} ligne {lineno} : adresse en double ({host}, déjà utilisée par {hosts[host]})")
            hosts[host] = pid
            printers.append(
                Printer(
                    id=pid,
                    name=(row.get("name") or pid).strip(),
                    host=host,
                    brand=(row.get("brand") or "").strip(),
                    model=(row.get("model") or "").strip(),
                    location=(row.get("location") or "").strip(),
                    user=(row.get("user") or row.get("utilisateur") or "").strip(),
                    enabled=enabled,
                )
            )
    return printers
