"""Accès SNMP via les binaires net-snmp (snmpwalk / snmpget).

Choix : les secrets SNMP ne passent jamais en ligne de commande (visibles dans `ps`). Ils sont écrits dans un
snmp.conf temporaire (répertoire 0700, fichier 0600) que net-snmp lit via SNMPCONFPATH, puis supprimé à la sortie.
"""
from __future__ import annotations

import atexit
import os
import re
import shutil
import subprocess
import tempfile
from typing import Callable, Protocol

from . import device
from .config import SnmpConfig
from .model import RawSupply

SUPPLIES_OID = "1.3.6.1.2.1.43.11.1.1"        # prtMarkerSuppliesEntry
SYS_DESCR_OID = "1.3.6.1.2.1.1.1.0"

_LINE = re.compile(r"^\.?(?P<oid>\d+(?:\.\d+)+)\s+(?P<val>.*)$")

# Valeurs « absentes » renvoyées par net-snmp en fin de table : elles ne doivent jamais écraser une vraie valeur.
_NO_VALUE = ("No more variables", "No Such Object", "No Such Instance")
# net-snmp affiche en hexadécimal (16 octets par ligne) toute chaîne contenant un octet non ASCII (ex. « é »).
_HEX = re.compile(r"^(?:[0-9A-Fa-f]{2}(?:\s+|$))+$")


class SnmpError(RuntimeError):
    """Échec d'une requête SNMP (hôte injoignable, authentification refusée, binaire absent…)."""


class SnmpClient(Protocol):
    def supplies(self, host: str) -> list[RawSupply]: ...

    def sys_descr(self, host: str) -> str | None: ...


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        value = value[1:-1]
    return value


def _decode_text(value: str) -> str:
    """Décode une chaîne affichée en hexadécimal par net-snmp ; renvoie la valeur inchangée sinon."""
    v = value.strip()
    if v.startswith("Hex-STRING:"):
        v = v[len("Hex-STRING:"):].strip()
    v = v.strip('"').strip()                           # net-snmp -Oq encadre la chaîne de guillemets
    if _HEX.match(v) and len(v.split()) >= 2:
        raw = bytes.fromhex("".join(v.split()))
        try:
            return raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            return raw.decode("latin-1").strip()
    return value


def _to_int(value: str) -> int | None:
    try:
        return int(value.strip())
    except ValueError:
        return None


# net-snmp écrit des messages d'information sur stderr (ex. « Created directory: … ») avant l'erreur réelle.
_NOISE = ("Created directory:",)


def _error_line(text: str | None) -> str:
    """Dernière ligne significative de la sortie d'erreur de net-snmp."""
    lines = [ln.strip() for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith(_NOISE)]
    return lines[-1][:200] if lines else ""



def parse_supplies(output: str) -> list[RawSupply]:
    """Transforme la sortie `snmpwalk -On -Oq` de prtMarkerSuppliesTable en lignes par consommable."""
    prefix = SUPPLIES_OID + "."
    rows: dict[str, dict[int, str]] = {}
    last: tuple[str, int] | None = None
    for line in output.splitlines():
        stripped = line.strip()
        m = _LINE.match(stripped)
        if not m:
            tail = stripped.strip('"').strip()
            if last and tail and _HEX.match(tail):     # suite d'une chaîne hexadécimale multi-lignes
                rows[last[0]][last[1]] += " " + tail
            continue
        if not m["oid"].startswith(prefix):
            last = None
            continue
        col, _, idx = m["oid"][len(prefix):].partition(".")
        if not col.isdigit() or not idx:
            last = None
            continue
        if m["val"].startswith(_NO_VALUE):             # fin de MIB : ne pas écraser la valeur déjà lue
            last = None
            continue
        rows.setdefault(idx, {})[int(col)] = _unquote(m["val"])
        last = (idx, int(col))

    def natural(idx: str) -> list[int]:
        return [int(part) for part in idx.split(".") if part.isdigit()]

    return [
        RawSupply(
            index=idx,
            klass=_to_int(cols.get(4, "")),
            stype=_to_int(cols.get(5, "")),
            description=_decode_text(cols.get(6, "")).strip(),
            unit=_to_int(cols.get(7, "")),
            max_capacity=_to_int(cols.get(8, "")),
            level=_to_int(cols.get(9, "")),
        )
        for idx, cols in sorted(rows.items(), key=lambda kv: natural(kv[0]))
    ]


class NetSnmpClient:
    def __init__(
        self,
        cfg: SnmpConfig,
        *,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        secrets: dict[str, str] | None = None,
    ) -> None:
        self.cfg = cfg
        self._run = runner
        secrets = cfg.secrets() if secrets is None else secrets
        self._dir = tempfile.mkdtemp(prefix="printwatch-snmp-")
        os.chmod(self._dir, 0o700)
        fd = os.open(os.path.join(self._dir, "snmp.conf"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(self._render_conf(secrets))
        atexit.register(self.close)

    def close(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)

    def _render_conf(self, secrets: dict[str, str]) -> str:
        if self.cfg.version in ("1", "2c"):
            return f'defCommunity "{secrets["community"]}"\n'
        lines = []
        if "auth" in secrets:
            lines.append(f'defAuthPassphrase "{secrets["auth"]}"')
        if "priv" in secrets:
            lines.append(f'defPrivPassphrase "{secrets["priv"]}"')
        return "\n".join(lines) + "\n"

    def _args(self) -> list[str]:
        c = self.cfg
        args = ["-On", "-Oq", "-m", "", "-t", str(c.timeout), "-r", str(c.retries)]
        if c.version in ("1", "2c"):
            return args + ["-v", c.version]
        args += ["-v", "3", "-u", c.user, "-l", c.level]
        if c.level != "noAuthNoPriv":
            args += ["-a", c.auth_proto]
        if c.level == "authPriv":
            args += ["-x", c.priv_proto]
        if c.context:
            args += ["-n", c.context]
        return args

    def _exec(self, cmd: list[str], *, partial: bool = False) -> str:
        """partial : accepte une réponse incomplète. En SNMPv1, un OID absent fait échouer la requête ; net-snmp la rejoue
        sans lui et renvoie les autres valeurs, mais avec un code de retour non nul."""
        env = {**os.environ, "SNMPCONFPATH": self._dir, "SNMP_PERSISTENT_DIR": self._dir}
        try:
            proc = self._run(
                cmd, capture_output=True, text=True, timeout=self.cfg.command_timeout, env=env, check=False
            )
        except FileNotFoundError as exc:
            raise SnmpError(f"binaire net-snmp introuvable ({cmd[0]}) : installer le paquet snmp") from exc
        except subprocess.TimeoutExpired as exc:
            raise SnmpError("délai dépassé") from exc
        if proc.returncode != 0 and not (partial and proc.stdout.strip()):
            msg = _error_line(proc.stderr) or _error_line(proc.stdout) or f"code de retour {proc.returncode}"
            raise SnmpError(msg)
        return proc.stdout

    def supplies(self, host: str) -> list[RawSupply]:
        return parse_supplies(self._exec([self.cfg.snmpwalk, "-Cc", *self._args(), host, SUPPLIES_OID]))

    def device_info(self, host: str) -> dict:
        """Fiche matérielle (voir device.py). Chaque requête est facultative : une table absente donne une liste vide."""
        def run(cmd: list[str]) -> str:
            try:
                return self._exec(cmd, partial=True)
            except SnmpError:
                return ""

        scalars = run([self.cfg.snmpget, *self._args(), "-Ot", "-Oe", host, *device.SCALARS.values()])
        if not scalars.strip():
            raise SnmpError("aucune donnée matérielle")
        error_state = run([self.cfg.snmpget, *self._args(), "-Ox", host, device.ERROR_STATE_OID])
        tables = {name: run([self.cfg.snmpwalk, "-Cc", *self._args(), "-Oe", host, oid]) for name, oid in device.TABLES.items()}
        return device.parse_device(scalars, error_state, tables)

    def sys_descr(self, host: str) -> str | None:
        try:
            out = self._exec([self.cfg.snmpget, *self._args(), "-Ov", host, SYS_DESCR_OID])
        except SnmpError:
            return None
        lines = [ln.strip() for ln in out.strip().splitlines() if ln.strip()]
        if not lines:
            return None
        joined = " ".join(ln.strip('"').strip() for ln in lines)
        decoded = _decode_text(joined)
        if decoded != joined:                          # chaîne hexadécimale multi-lignes
            return decoded[:200]
        return _unquote(lines[0])[:200]
