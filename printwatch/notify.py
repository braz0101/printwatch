"""Notifications : un seul courriel récapitulatif par cycle, jamais un par événement."""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from typing import Any, Mapping, Protocol, Sequence

from .config import SmtpConfig
from .model import EventType

log = logging.getLogger(__name__)

_ORDER = [EventType.CRITICAL, EventType.DOWN, EventType.WARN, EventType.UP, EventType.RESOLVED, EventType.REPLACED]
_TITLE = {
    EventType.CRITICAL: "CRITIQUE",
    EventType.DOWN: "INJOIGNABLE",
    EventType.WARN: "AVERTISSEMENT",
    EventType.UP: "RÉTABLI (imprimante de nouveau joignable)",
    EventType.RESOLVED: "RÉSOLU",
    EventType.REPLACED: "REMPLACEMENT DÉTECTÉ",
}


class Notifier(Protocol):
    def send(self, events: Sequence[Mapping[str, Any]]) -> None: ...


def render(events: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    """Renvoie (sujet, corps) en texte brut."""
    by_type: dict[str, list[Mapping[str, Any]]] = {}
    for ev in events:
        by_type.setdefault(str(ev["type"]), []).append(ev)

    n_crit = len(by_type.get(EventType.CRITICAL.value, [])) + len(by_type.get(EventType.DOWN.value, []))
    n_warn = len(by_type.get(EventType.WARN.value, []))
    parts = []
    if n_crit:
        parts.append(f"{n_crit} critique(s)")
    if n_warn:
        parts.append(f"{n_warn} avertissement(s)")
    subject = "[PrintWatch] " + (", ".join(parts) if parts else f"{len(events)} information(s)")

    lines: list[str] = []
    for etype in _ORDER:
        group = by_type.get(etype.value)
        if not group:
            continue
        lines.append(f"== {_TITLE[etype]} ({len(group)}) ==")
        for ev in group:
            place = f" – {ev['location']}" if ev.get("location") else ""
            lines.append(f"- {ev['printer_name']}{place} ({ev['host']}) : {ev['message']}")
        lines.append("")
    lines.append(f"Relevé du {datetime.fromtimestamp(max(float(e['ts']) for e in events)):%d/%m/%Y %H:%M}.")
    return subject, "\n".join(lines)


class LogNotifier:
    """Repli quand le SMTP n'est pas configuré : les alertes vont dans les journaux."""

    def send(self, events: Sequence[Mapping[str, Any]]) -> None:
        subject, body = render(events)
        log.warning("%s\n%s", subject, body)


class SmtpNotifier:
    def __init__(self, cfg: SmtpConfig) -> None:
        self.cfg = cfg

    def send(self, events: Sequence[Mapping[str, Any]]) -> None:
        subject, body = render(events)
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.cfg.sender
        msg["To"] = ", ".join(self.cfg.recipients)
        msg.set_content(body)
        ctx = ssl.create_default_context()
        # Port 465 : TLS implicite (SMTPS) ; sinon connexion en clair, éventuellement relevée par STARTTLS.
        smtp_cls = (lambda: smtplib.SMTP_SSL(self.cfg.host, self.cfg.port, timeout=20, context=ctx)) if self.cfg.port == 465 \
            else (lambda: smtplib.SMTP(self.cfg.host, self.cfg.port, timeout=20))
        with smtp_cls() as smtp:
            if self.cfg.starttls and self.cfg.port != 465:
                smtp.starttls(context=ctx)
            if self.cfg.user:
                password = os.environ.get(self.cfg.password_env, "")
                if not password:
                    raise RuntimeError(f"variable d'environnement {self.cfg.password_env} absente : authentification SMTP impossible")
                smtp.login(self.cfg.user, password)
            smtp.send_message(msg)


def make_notifier(cfg: SmtpConfig) -> Notifier:
    return SmtpNotifier(cfg) if cfg.enabled else LogNotifier()
