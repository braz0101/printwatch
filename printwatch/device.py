"""Fiche matérielle d'une imprimante : identité, compteur, état, alertes, bacs et capots (Printer MIB RFC 3805, Host Resources MIB).

Lecture seule et jamais bloquante : une valeur absente de l'imprimante est simplement omise. Les libellés lisibles sont produits
par l'interface ; on stocke ici des codes stables (clés anglaises de la RFC).
"""
from __future__ import annotations

import re
from typing import Any

SCALARS = {
    "sys_name": "1.3.6.1.2.1.1.5.0",
    "sys_location": "1.3.6.1.2.1.1.6.0",
    "sys_contact": "1.3.6.1.2.1.1.4.0",
    "uptime": "1.3.6.1.2.1.1.3.0",                       # timeticks (centièmes de seconde), lus avec -Ot
    "serial": "1.3.6.1.2.1.43.5.1.1.17.1",               # prtGeneralSerialNumber
    "page_count": "1.3.6.1.2.1.43.10.2.1.4.1.1",         # prtMarkerLifeCount : pages imprimées depuis la mise en service
    "device_status": "1.3.6.1.2.1.25.3.2.1.5.1",         # hrDeviceStatus
    "printer_status": "1.3.6.1.2.1.25.3.5.1.1.1",        # hrPrinterStatus
    "device_descr": "1.3.6.1.2.1.25.3.2.1.3.1",          # hrDeviceDescr : modèle exact
}
ERROR_STATE_OID = "1.3.6.1.2.1.25.3.5.1.2.1"             # hrPrinterDetectedErrorState (BITS), lu avec -Ox
TABLES = {
    "trays": "1.3.6.1.2.1.43.8.2.1",                     # prtInputEntry
    "covers": "1.3.6.1.2.1.43.6.1.1",                    # prtCoverEntry
    "alerts": "1.3.6.1.2.1.43.18.1.1",                   # prtAlertEntry
    "display": "1.3.6.1.2.1.43.16.5.1.2",                # prtConsoleDisplayBufferText
    "mac": "1.3.6.1.2.1.2.2.1.6",                        # ifPhysAddress
}

DEVICE_STATUS = {1: "unknown", 2: "running", 3: "warning", 4: "testing", 5: "down"}
PRINTER_STATUS = {1: "other", 2: "unknown", 3: "idle", 4: "printing", 5: "warmup"}
# Bit 0 = bit de poids fort du premier octet (type BITS).
ERROR_BITS = ("lowPaper", "noPaper", "lowToner", "noToner", "doorOpen", "jammed", "offline", "serviceRequested",
              "inputTrayMissing", "outputTrayMissing", "markerSupplyMissing", "outputNearFull", "outputFull",
              "inputTrayEmpty", "overduePreventMaint")
COVER_STATUS = {1: "other", 3: "open", 4: "closed", 5: "open", 6: "closed"}       # interlock ouvert = capot ouvert
SEVERITY = {1: "other", 3: "critical", 4: "warning", 5: "event"}                   # 5 : changement d'état ponctuel

_LINE = re.compile(r"^\.?(?P<oid>\d+(?:\.\d+)+)\s*(?P<val>.*)$")
_NO_VALUE = ("No more variables", "No Such Object", "No Such Instance", "End of MIB")
_HEX = re.compile(r"^(?:[0-9A-Fa-f]{2}(?:\s+|$))+$")


def _text(value: str) -> str:
    """Chaîne SNMP lisible : guillemets retirés, chaîne hexadécimale (octets non ASCII) décodée."""
    v = value.strip()
    if v.startswith("Hex-STRING:"):
        v = v[len("Hex-STRING:"):].strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1]
    if _HEX.match(v.strip()) and len(v.split()) >= 2:
        raw = bytes.fromhex("".join(v.split())).rstrip(b"\x00")
        try:
            return raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            return raw.decode("latin-1").strip()
    return v.strip()


def _int(value: str | None) -> int | None:
    try:
        return int((value or "").strip())
    except ValueError:
        return None


def parse_values(output: str) -> dict[str, str]:
    """Sortie `-On -Oq` (get ou walk) → {oid: valeur brute}. Les marqueurs « pas de valeur » sont ignorés."""
    out: dict[str, str] = {}
    last: str | None = None
    for line in output.splitlines():
        stripped = line.strip()
        m = _LINE.match(stripped)
        if not m:
            tail = stripped.strip('"').strip()
            if last and tail and _HEX.match(tail):             # suite d'une chaîne hexadécimale multi-lignes
                out[last] += " " + tail
            continue
        if m["val"].startswith(_NO_VALUE):
            last = None
            continue
        out[m["oid"]] = m["val"]
        last = m["oid"]
    return out


def parse_table(output: str, base: str) -> dict[str, dict[int, str]]:
    """Walk d'une table `base` → {index de ligne: {colonne: valeur brute}}."""
    prefix = base + "."
    rows: dict[str, dict[int, str]] = {}
    for oid, val in parse_values(output).items():
        if not oid.startswith(prefix):
            continue
        col, _, idx = oid[len(prefix):].partition(".")
        if col.isdigit() and idx:
            rows.setdefault(idx, {})[int(col)] = val
    return rows


def decode_error_state(value: str | None) -> list[str]:
    """hrPrinterDetectedErrorState lu en hexadécimal (`-Ox`) → liste des indicateurs levés, dans l'ordre de la RFC."""
    if not value:
        return []
    v = value.strip()
    if v.startswith("Hex-STRING:"):
        v = v[len("Hex-STRING:"):]
    v = v.replace('"', "").strip()
    if not v or not _HEX.match(v):
        return []
    data = bytes.fromhex("".join(v.split()))
    return [name for bit, name in enumerate(ERROR_BITS) if bit // 8 < len(data) and data[bit // 8] & (0x80 >> (bit % 8))]


def normalize_mac(value: str) -> str | None:
    """`4:e:3c:e9:a:cb` (format net-snmp) → `04:0E:3C:E9:0A:CB` ; None pour une adresse vide ou nulle."""
    parts = [p for p in re.split(r"[:\s-]+", value.strip().strip('"')) if p]
    if len(parts) != 6:
        return None
    try:
        octets = [int(p, 16) for p in parts]
    except ValueError:
        return None
    if not any(octets) or any(o > 255 for o in octets):
        return None
    return ":".join(f"{o:02X}" for o in octets)


def _enum(value: str | None, table: dict[int, str]) -> str | None:
    """Valeur énumérée : code numérique, ou nom (`running(2)`, `idle`) si net-snmp a chargé la MIB malgré `-m ""`."""
    if value is None:
        return None
    v = value.strip()
    if (n := _int(v)) is not None:
        return table.get(n)
    m = re.match(r"^([A-Za-z]+)(?:\((\d+)\))?$", v)
    if not m:
        return None
    if m[2]:
        return table.get(int(m[2]))
    return m[1] if m[1] in table.values() else None


def _natural(idx: str) -> list[int]:
    return [int(x) for x in idx.split(".") if x.isdigit()]


def parse_device(scalars: str, error_state: str, tables: dict[str, str]) -> dict[str, Any]:
    """Assemble la fiche matérielle à partir des sorties net-snmp. Seules les valeurs présentes sont renseignées."""
    vals = parse_values(scalars)
    get = {name: vals.get(oid) for name, oid in SCALARS.items()}
    info: dict[str, Any] = {}

    for key in ("sys_name", "sys_location", "sys_contact", "serial"):
        if get[key] is not None and _text(get[key]):
            info[key] = _text(get[key])[:120]
    if get["device_descr"] is not None and _text(get["device_descr"]):
        info["model"] = _text(get["device_descr"])[:120]
    if (ticks := _int(get["uptime"])) is not None and ticks >= 0:
        info["uptime_s"] = ticks // 100
    if (pages := _int(get["page_count"])) is not None and pages >= 0:
        info["page_count"] = pages
    if ds := _enum(get["device_status"], DEVICE_STATUS):
        info["device_status"] = ds
    if ps := _enum(get["printer_status"], PRINTER_STATUS):
        info["printer_status"] = ps

    info["errors"] = decode_error_state(parse_values(error_state).get(ERROR_STATE_OID))

    # Message affiché sur l'écran de l'imprimante (une ligne par entrée non vide).
    display = parse_values(tables.get("display", ""))
    info["display"] = [t[:80] for _, v in sorted(display.items(), key=lambda kv: _natural(kv[0])) if (t := _text(v))][:5]

    trays = []
    for idx, cols in sorted(parse_table(tables.get("trays", ""), TABLES["trays"]).items(), key=lambda kv: _natural(kv[0])):
        name = _text(cols.get(13, "")) or _text(cols.get(18, "")) or f"Bac {idx.split('.')[-1]}"
        trays.append({"name": name[:60], "media": _text(cols.get(12, ""))[:60] or None,
                      "level": _int(cols.get(10)), "max": _int(cols.get(9))})
    info["trays"] = trays

    covers = []
    for idx, cols in sorted(parse_table(tables.get("covers", ""), TABLES["covers"]).items(), key=lambda kv: _natural(kv[0])):
        name = _text(cols.get(2, "")) or f"Capot {idx.split('.')[-1]}"
        covers.append({"name": name[:60], "status": COVER_STATUS.get(_int(cols.get(3)) or 0, "unknown")})
    info["covers"] = covers

    alerts = []
    for idx, cols in sorted(parse_table(tables.get("alerts", ""), TABLES["alerts"]).items(), key=lambda kv: _natural(kv[0])):
        code = _int(cols.get(7))
        if code is None:
            continue
        alert = {"severity": SEVERITY.get(_int(cols.get(2)) or 0, "other"), "group": _int(cols.get(4)),
                 "code": code, "description": _text(cols.get(8, ""))[:120] or None}
        if alert not in alerts:                                  # une même alerte peut figurer sur plusieurs lignes (HP)
            alerts.append(alert)
    info["alerts"] = alerts

    macs = [m for _, v in sorted(parse_values(tables.get("mac", "")).items(), key=lambda kv: _natural(kv[0])) if (m := normalize_mac(v))]
    if macs:
        info["mac"] = macs[0]
    return info
