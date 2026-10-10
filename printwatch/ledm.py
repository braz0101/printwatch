"""Complément HP : niveaux des cartouches lus dans l'interface web (LEDM, /DevMgmt/ConsumableConfigDyn.xml).

Pourquoi : plusieurs LaserJet Pro (M479…) répondent -2 (« inconnu ») en SNMP pour les cartouches très basses ou non HP,
alors que leur interface web publie un état et un pourcentage exploitables. Ce module ne REMPLACE jamais une valeur SNMP valide :
il ne comble que les niveaux que la Printer MIB laisse inconnus. Toute erreur (port fermé, XML inattendu) est silencieuse côté
collecte : on retombe sur la valeur SNMP, c'est-à-dire « inconnu ».
"""
from __future__ import annotations

import logging
import ssl
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from typing import Callable, Sequence
from urllib.parse import urlparse

from .model import RawSupply
from .normalize import guess_color

log = logging.getLogger(__name__)

PATH = "/DevMgmt/ConsumableConfigDyn.xml"
MAX_BYTES = 262_144                                   # le XML d'une imprimante fait ~16 Ko : tout ce qui dépasse est suspect
_DD = "{http://www.hp.com/schemas/imaging/con/dictionaries/1.0/}"
_CC = "{http://www.hp.com/schemas/imaging/con/ledm/consumableconfigdyn/2007/11/19}"
_LABEL_COLOR = {"K": "black", "C": "cyan", "M": "magenta", "Y": "yellow"}      # « CMY » (kit couleur) : ignoré
_UNUSABLE_STATES = {"missing", "notpresent", "unknown", "invalid"}


class LedmError(Exception):
    pass


@dataclass(frozen=True)
class LedmSupply:
    color: str | None          # black / cyan / magenta / yellow
    product: str               # référence cartouche, ex. W2033A
    pct: int                   # ConsumablePercentageLevelRemaining (0-100)
    state: str                 # ok / low / veryLow / nonHP …
    brand: str                 # HP / clone …


def parse(data: bytes) -> list[LedmSupply]:
    """Extrait les toners du XML. Refuse les DOCTYPE/ENTITY (expansion d'entités) : la source est un équipement réseau."""
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in data.lower():
        raise LedmError("XML refusé (DOCTYPE/ENTITY)")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise LedmError(f"XML illisible : {exc}") from exc
    out: list[LedmSupply] = []
    for ci in root.iter(f"{_CC}ConsumableInfo"):
        if (ci.findtext(f"{_DD}ConsumableTypeEnum") or "").strip().lower() != "toner":
            continue
        color = _LABEL_COLOR.get((ci.findtext(f"{_DD}ConsumableLabelCode") or "").strip().upper())
        state = (ci.findtext(f"{_DD}ConsumableLifeState/{_DD}ConsumableState") or "").strip()
        try:
            pct = int((ci.findtext(f"{_DD}ConsumablePercentageLevelRemaining") or "").strip())
        except ValueError:
            continue
        if color is None or not 0 <= pct <= 100 or state.lower() in _UNUSABLE_STATES:
            continue
        out.append(LedmSupply(color, (ci.findtext(f"{_DD}ProductNumber") or "").strip(), pct, state,
                              (ci.findtext(f"{_DD}ConsumableLifeState/{_DD}Brand") or "").strip()))
    return out


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """Suit les redirections HTTP → HTTPS de l'imprimante, jamais vers un autre hôte (une imprimante compromise ne doit pas
    pouvoir faire interroger le réseau interne par le serveur)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        if urlparse(newurl).hostname != urlparse(req.full_url).hostname:
            raise LedmError("redirection vers un autre hôte refusée")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _get(url: str, timeout: float, context: ssl.SSLContext | None, deadline_s: float | None = None) -> bytes:
    """GET borné : taille maximale ET durée totale (un équipement qui goutte les octets ne doit pas bloquer un thread de collecte)."""
    opener = urllib.request.build_opener(_SameHostRedirect, urllib.request.HTTPSHandler(context=context))
    req = urllib.request.Request(url, headers={"Accept": "application/xml", "User-Agent": "PrintWatch"})
    deadline = time.monotonic() + (deadline_s if deadline_s is not None else max(timeout * 2, 5.0))
    buf = bytearray()
    with opener.open(req, timeout=timeout) as resp:   # noqa: S310 : URL construite depuis la liste d'imprimantes, schémas http/https
        while chunk := resp.read1(4096):                # rend la main dès que des octets arrivent (read(n) attendrait les n octets)
            buf += chunk
            if len(buf) > MAX_BYTES:
                raise LedmError("réponse trop volumineuse")
            if time.monotonic() > deadline:
                raise LedmError("réponse trop lente")
    return bytes(buf)


def fetch(host: str, timeout: float = 3.0, deadline_s: float | None = None) -> list[LedmSupply]:
    """HTTP d'abord ; HTTPS (certificat auto-signé de l'imprimante, non vérifié) si le port 80 est fermé. Lecture seule, sans identifiants."""
    try:
        return parse(_get(f"http://{host}{PATH}", timeout, None, deadline_s))
    except LedmError:
        raise
    except Exception:
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        try:
            return parse(_get(f"https://{host}{PATH}", timeout, ctx, deadline_s))
        except LedmError:
            raise
        except Exception as exc:
            raise LedmError(f"interface web HP injoignable : {exc}") from exc


def is_unknown(raw: RawSupply) -> bool:
    """Niveau SNMP inexploitable (-2 inconnu, -1 autre, absent) sur un consommable qui se vide. -3 (« il en reste ») est une réponse valide."""
    return raw.klass != 4 and raw.stype not in (4, 8) and (raw.level is None or raw.level in (-1, -2))


def complete(raws: Sequence[RawSupply], ledm: Sequence[LedmSupply]) -> list[RawSupply]:
    """Comble les niveaux SNMP inconnus avec le pourcentage HP du même toner (référence cartouche, sinon couleur)."""
    out: list[RawSupply] = []
    for raw in raws:
        match = None
        if is_unknown(raw):
            desc = raw.description.upper()
            match = next((s for s in ledm if s.product and s.product.upper() in desc), None) \
                or next((s for s in ledm if s.color and s.color == guess_color(raw.description)), None)
        out.append(replace(raw, unit=19, max_capacity=100, level=match.pct) if match else raw)
    return out


Fetcher = Callable[[str], Sequence[LedmSupply]]
