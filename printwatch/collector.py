"""Un cycle de collecte : interrogation concurrente, mise à jour de l'état, événements, notification."""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Sequence

from .alerts import Thresholds, next_level, transition
from . import ledm
from .config import CollectorConfig
from .forecast import estimate
from .model import EventType, Kind, Level, Printer, RawSupply, Supply
from .normalize import normalize
from .notify import Notifier
from .snmp import SnmpClient, SnmpError
from .storage import Storage

log = logging.getLogger(__name__)

_NO_EMAIL = {EventType.REPLACED}   # tracés dans le journal mais sans courriel
DAY = 86400.0
PURGE_EVERY_S = 3600.0            # le nettoyage parcourt l'historique : une fois par heure suffit


@dataclass
class CycleReport:
    polled: int = 0
    failed: int = 0
    events: int = 0
    notified: int = 0
    duration_s: float = 0.0


def describe(s: Supply, etype: EventType) -> str:
    """Libellé lisible d'un événement ; pour un réceptacle on parle de remplissage, pas de reste."""
    pct = s.pct_remaining
    if pct is None:
        return s.description
    if s.kind is Kind.RECEPTACLE:
        state = f"{100 - pct:.0f} % plein"
    else:
        state = f"{pct:.0f} % restant"
    if etype is EventType.REPLACED:
        return f"{s.description} : remplacement détecté ({state})"
    return f"{s.description} : {state}"


class Collector:
    def __init__(
        self,
        storage: Storage,
        client: SnmpClient,
        thresholds: Thresholds,
        notifier: Notifier,
        cfg: CollectorConfig,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        hp_fetch: ledm.Fetcher | None = None,
    ) -> None:
        self.storage = storage
        self.client = client
        self.th = thresholds
        self.notifier = notifier
        self.cfg = cfg
        self.clock = clock
        self.sleep = sleep
        self.hp_fetch = hp_fetch or ledm.fetch
        self._last_purge = float("-inf")

    # --- interrogation (I/O, exécutée en parallèle) ----------------------------------------------------------
    def _poll(self, p: Printer) -> tuple[Printer, list[RawSupply] | None, str | None, str | None]:
        try:
            raws = self.client.supplies(p.host)
            if not raws:
                return p, None, None, "l'imprimante répond mais ne publie aucune table de consommables"
            descr = self.client.sys_descr(p.host)
            return p, self._complete_hp(p, raws, descr), descr, None
        except SnmpError as exc:
            return p, None, None, str(exc)
        except Exception as exc:  # défaut de programmation : ne doit pas faire tomber tout le cycle
            log.exception("erreur inattendue pendant l'interrogation de %s", p.name)
            return p, None, None, f"erreur interne : {exc}"

    def _complete_hp(self, p: Printer, raws: list[RawSupply], descr: str | None) -> list[RawSupply]:
        """HP : si la table SNMP laisse des toners « inconnus », les combler via l'interface web. Jamais bloquant."""
        if not self.cfg.hp_ledm or not any(ledm.is_unknown(r) for r in raws):
            return raws
        if "HP" not in f"{p.brand} {descr or ''}".upper().split() and "HEWLETT" not in f"{p.brand} {descr or ''}".upper():
            return raws
        try:
            return ledm.complete(raws, self.hp_fetch(p.host))
        except Exception as exc:                                  # SNMP reste la référence : on garde ses valeurs
            log.debug("%s : complément HP indisponible (%s)", p.name, exc)
            return raws

    # --- application des résultats (séquentiel : un seul écrivain SQLite) ------------------------------------
    def _event(
        self, ts: float, p: Printer, key: str | None, etype: EventType, pct: float | None, message: str
    ) -> None:
        self.storage.add_event(ts, p.id, key, etype, pct, message, notify=etype not in _NO_EMAIL)

    def _apply_success(self, p: Printer, raws: Sequence[RawSupply], descr: str | None, ts: float) -> int:
        was_down = self.storage.is_down(p.id)
        self.storage.record_success(p.id, ts, descr)
        created = 0
        if was_down:
            self._event(ts, p, None, EventType.UP, None, "de nouveau joignable")
            created += 1

        for raw in raws:
            s = normalize(raw)
            prev = self.storage.get_supply(p.id, s.key)
            prev_level = Level(prev["alert_level"]) if prev else Level.OK
            prev_pct = prev["pct"] if prev else None
            new_level = prev_level

            if s.pct_remaining is not None:
                pct = s.pct_remaining
                replaced = prev_pct is not None and pct - prev_pct >= self.cfg.replacement_jump
                new_level = next_level(prev_level, pct, self.th, replaced=replaced)
                etype = EventType.REPLACED if replaced else transition(prev_level, new_level)
                if etype is not None:
                    self._event(ts, p, s.key, etype, pct, describe(s, etype))
                    created += 1
            self.storage.upsert_supply(p.id, s, int(new_level), ts)
            self._store_forecast(p, s, ts)
        return created

    def _store_forecast(self, p: Printer, s: Supply, ts: float) -> None:
        """Calcule la rupture estimée UNE fois par cycle (35 jours d'historique) : l'API se contente de la lire."""
        est = None
        if s.pct_remaining is not None:
            est = estimate(self.storage.history(p.id, s.key, ts - 35 * DAY), replacement_jump=self.cfg.replacement_jump)
        self.storage.set_forecast(p.id, s.key, est.days_left if est else None, est.confidence if est else None)

    def _apply_failure(self, p: Printer, error: str, ts: float, grace: bool = False) -> int:
        failures = self.storage.record_failure(p.id, error)
        if grace:                                                  # reprise après interruption : réseau peut-être pas encore revenu
            return 0
        if failures >= self.cfg.down_after and not self.storage.is_down(p.id):
            self.storage.mark_down(p.id)
            self._event(ts, p, None, EventType.DOWN, None, f"relevé impossible depuis {failures} cycles : {error}")
            return 1
        return 0

    def _flush(self) -> int:
        pending = self.storage.pending_events()
        if not pending:
            return 0
        try:
            self.notifier.send(pending)
        except Exception:
            log.exception("échec de l'envoi des alertes ; elles seront renvoyées au prochain cycle")
            return 0
        self.storage.mark_notified(ev["id"] for ev in pending)
        return len(pending)

    # --- cycle -----------------------------------------------------------------------------------------------
    def run_cycle(self, printers: Sequence[Printer]) -> CycleReport:
        start = self.clock()
        report = CycleReport()
        active = [p for p in printers if p.enabled]
        for p in printers:
            self.storage.ensure_printer(p)               # crée si inconnue ; la base (éditée depuis le dashboard) fait foi

        if active:
            last = self.storage.last_cycle()
            gap = start - last["ts"] if last else 0.0
            grace = gap > 2 * self.cfg.interval_s + 120
            if grace:
                log.warning("collecte reprise après %.0f min d'interruption : échecs de ce cycle non comptés vers « injoignable »", gap / 60)
            with ThreadPoolExecutor(max_workers=min(self.cfg.workers, len(active))) as pool:
                results = list(pool.map(self._poll, active))
                failed = [r[0] for r in results if r[1] is None]
                if failed and self.cfg.retry_delay_s > 0:          # une seule relance : perte UDP ponctuelle, imprimante qui se réveille
                    self.sleep(self.cfg.retry_delay_s)
                    again = {r[0].id: r for r in pool.map(self._poll, failed)}
                    results = [again.get(r[0].id, r) if r[1] is None else r for r in results]
            ts = self.clock()
            for p, raws, descr, error in results:
                report.polled += 1
                if raws is None:
                    report.failed += 1
                    log.warning("%s : %s", p.name, error)
                    report.events += self._apply_failure(p, error or "inconnu", ts, grace)
                else:
                    report.events += self._apply_success(p, raws, descr, ts)

        if start - self._last_purge >= PURGE_EVERY_S:
            self.storage.purge(self.clock() - self.cfg.retention_days * 86400)
            self._last_purge = start
        report.notified = self._flush()
        report.duration_s = self.clock() - start
        self.storage.record_cycle(self.clock(), report.polled, report.failed, report.events, report.duration_s)
        return report
