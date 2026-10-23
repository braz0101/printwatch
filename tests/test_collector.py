import pytest

from printwatch.alerts import Thresholds
from printwatch.collector import Collector
from printwatch.config import CollectorConfig
from printwatch.model import Printer, RawSupply
from printwatch.snmp import SnmpError
from printwatch.storage import Storage


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t

    def advance(self, hours=1):
        self.t += hours * 3600


class FakeClient:
    def __init__(self):
        self.data = {}

    def supplies(self, host):
        v = self.data[host]
        if isinstance(v, Exception):
            raise v
        return v

    def sys_descr(self, host):
        return "FAKE printer"


class FakeNotifier:
    def __init__(self):
        self.batches, self.fail = [], False

    def send(self, events):
        if self.fail:
            raise RuntimeError("smtp down")
        self.batches.append([dict(e) for e in events])


def ink(level, key="1.1", desc="Black Ink"):
    return RawSupply(key, 3, 5, desc, 19, 100, level)


def box(level):
    return RawSupply("1.9", 4, 4, "Maintenance Box", 19, 100, level)


@pytest.fixture
def env():
    clock, client, notifier = Clock(), FakeClient(), FakeNotifier()
    storage = Storage(":memory:")
    collector = Collector(storage, client, Thresholds(15, 5, 5), notifier, CollectorConfig(down_after=3, retry_delay_s=0), clock)
    printer = Printer("p1", "Compta-1", "10.0.0.5", "Epson", "WF-C879R", "Bureau 12")
    return collector, storage, client, notifier, clock, printer


def types(notifier):
    return [e["type"] for batch in notifier.batches for e in batch]


def cycle(env, *supplies, hours=1):
    collector, _, client, _, clock, printer = env
    client.data[printer.host] = list(supplies)
    clock.advance(hours)
    return collector.run_cycle([printer])


def test_warn_then_crit_then_replacement_without_duplicates(env):
    _, storage, _, notifier, _, _ = env
    cycle(env, ink(60))
    assert types(notifier) == []
    cycle(env, ink(14))
    cycle(env, ink(13))                      # même état : pas de nouveau courriel
    assert types(notifier) == ["warn"]
    cycle(env, ink(4))
    assert types(notifier) == ["warn", "critical"]
    cycle(env, ink(3))
    assert types(notifier) == ["warn", "critical"]
    cycle(env, ink(100))                     # cartouche changée
    assert types(notifier) == ["warn", "critical"]          # le remplacement est tracé mais pas envoyé
    assert storage.recent_events(1)[0]["type"] == "replaced"
    assert storage.get_supply("p1", "1.1")["alert_level"] == 0
    cycle(env, ink(99))
    assert types(notifier) == ["warn", "critical"]


def test_first_observation_below_threshold_alerts(env):
    cycle(env, ink(10))
    assert types(env[3]) == ["warn"]


def test_hysteresis_avoids_flapping(env):
    notifier = env[3]
    cycle(env, ink(14))
    cycle(env, ink(16))                      # remonte un peu : pas de « résolu »
    cycle(env, ink(14))                      # redescend : pas de nouvelle alerte
    assert types(notifier) == ["warn"]
    cycle(env, ink(25))                      # nettement au-dessus du seuil + hystérésis, sans saut de remplacement
    assert types(notifier) == ["warn", "resolved"]


def test_receptacle_alerts_when_nearly_full(env):
    notifier = env[3]
    cycle(env, ink(80), box(50))
    assert types(notifier) == []
    cycle(env, ink(80), box(96))
    assert types(notifier) == ["critical"]
    assert "96 % plein" in notifier.batches[-1][0]["message"]


def test_several_events_in_one_cycle_are_one_batch(env):
    notifier = env[3]
    cycle(env, ink(10, "1.1", "Black"), ink(3, "1.2", "Cyan"), box(97))
    assert len(notifier.batches) == 1 and len(notifier.batches[0]) == 3


def test_down_after_three_failures_then_recovery(env):
    collector, storage, client, notifier, clock, printer = env
    cycle(env, ink(60))
    for _ in range(2):
        client.data[printer.host] = SnmpError("Timeout")
        clock.advance()
        collector.run_cycle([printer])
    assert types(notifier) == []
    for _ in range(3):                       # 3e échec : alerte, puis aucun doublon
        client.data[printer.host] = SnmpError("Timeout")
        clock.advance()
        collector.run_cycle([printer])
    assert types(notifier) == ["down"]
    cycle(env, ink(60))
    assert types(notifier) == ["down", "up"]
    assert not storage.is_down("p1")


def test_empty_table_counts_as_failure(env):
    collector, _, client, _, clock, printer = env
    client.data[printer.host] = []
    r = collector.run_cycle([printer])
    assert r.failed == 1


def test_notifier_failure_keeps_events_for_retry(env):
    _, storage, _, notifier, _, _ = env
    notifier.fail = True
    cycle(env, ink(10))
    assert len(storage.pending_events()) == 1
    notifier.fail = False
    cycle(env, ink(10))
    assert types(notifier) == ["warn"] and storage.pending_events() == []


def test_unknown_values_never_alert_and_keep_previous_state(env):
    notifier = env[3]
    cycle(env, ink(10))
    cycle(env, ink(-3))
    cycle(env, ink(-2))
    assert types(notifier) == ["warn"]


def test_disabled_printer_is_skipped(env):
    collector, _, client, _, _, printer = env
    off = Printer("p2", "Archive", "10.0.0.6", enabled=False)
    client.data[printer.host] = [ink(50)]
    r = collector.run_cycle([printer, off])
    assert r.polled == 1


def test_storage_creates_missing_parent_directory(tmp_path):
    db = tmp_path / "data" / "sub" / "printwatch.db"
    storage = Storage(str(db))
    storage.close()
    assert db.is_file()


def test_each_cycle_is_recorded_as_heartbeat(env):
    _, storage, client, _, clock, printer = env
    assert storage.last_cycle() is None
    cycle(env, ink(50))
    cyc = storage.last_cycle()
    assert (cyc["polled"], cyc["failed"], cyc["ts"]) == (1, 0, clock.t)
    client.data[printer.host] = SnmpError("timeout")           # un cycle sans réponse est aussi enregistré
    clock.advance(1)
    env[0].run_cycle([printer])
    assert storage.last_cycle()["failed"] == 1


# --- complément HP (LEDM), relance et reprise -----------------------------------------------------------------
from printwatch.ledm import LedmSupply  # noqa: E402


def hp_env(fetch):
    clock, client, notifier = Clock(), FakeClient(), FakeNotifier()
    storage = Storage(":memory:")
    collector = Collector(storage, client, Thresholds(15, 5, 5), notifier, CollectorConfig(retry_delay_s=0), clock,
                          sleep=lambda s: None, hp_fetch=fetch)
    return collector, storage, client, notifier, clock, Printer("hp", "HP M479", "10.0.0.9", "HP", "M479", "")


def test_hp_unknown_levels_are_completed_and_alert(env_=None):
    calls = []
    def fetch(host):
        calls.append(host)
        return [LedmSupply("cyan", "W2031A", 0, "veryLow", "HP")]
    env = hp_env(fetch)
    cycle(env, ink(-2, "1.2", "Cyan Cartridge HP W2031A"), ink(-2, "1.1", "Black Cartridge HP W2030A"))
    s = {r["key"]: r for r in env[1].conn.execute("SELECT key, pct, alert_level FROM supplies")}
    assert calls == ["10.0.0.9"]
    assert (s["1.2"]["pct"], s["1.2"]["alert_level"]) == (0, 2)       # comblé par l'interface web HP : critique
    assert s["1.1"]["pct"] is None                                    # pas de correspondance : reste inconnu
    assert "critical" in types(env[3])


def test_hp_fetch_failure_or_valid_snmp_keeps_snmp_and_non_hp_never_fetches():
    def boom(host):
        raise RuntimeError("port 80 fermé")
    env = hp_env(boom)
    cycle(env, ink(-2, "1.1", "Black Cartridge HP W2030A"))             # échec LEDM : on garde « inconnu », le cycle réussit
    assert env[1].conn.execute("SELECT pct FROM supplies").fetchone()["pct"] is None

    called = []
    env = hp_env(lambda h: called.append(h) or [])
    cycle(env, ink(50))                                                  # HP mais niveau SNMP valide : aucune requête web
    assert called == []
    collector, storage, client, notifier, clock, printer = env
    epson = Printer("ep", "Epson", "10.0.0.8", "EPSON", "WF", "")
    client.data[epson.host] = [ink(-2)]
    collector.run_cycle([epson])                                         # niveau inconnu mais marque non HP : aucune requête web
    assert called == []


def test_failed_printer_is_retried_once_in_the_same_cycle():
    class Flaky(FakeClient):
        def __init__(self):
            super().__init__(); self.n = 0
        def supplies(self, host):
            self.n += 1
            if self.n == 1:
                raise SnmpError("timeout")
            return [ink(50)]
    clock, client = Clock(), Flaky()
    c = Collector(Storage(":memory:"), client, Thresholds(15, 5, 5), FakeNotifier(), CollectorConfig(retry_delay_s=1), clock, sleep=lambda s: None)
    r = c.run_cycle([Printer("p", "P", "10.0.0.1", "Epson", "WF", "")])
    assert (r.polled, r.failed, client.n) == (1, 0, 2)


def test_failures_after_a_long_interruption_do_not_mark_down(env):
    collector, storage, client, notifier, clock, printer = env
    cycle(env, ink(50))
    client.data[printer.host] = SnmpError("timeout")
    for _ in range(2):
        clock.advance(0.5); collector.run_cycle([printer])             # 2 échecs normaux
    clock.advance(5); collector.run_cycle([printer])                  # reprise après 5 h : 3e échec toléré
    assert "down" not in types(notifier)
    clock.advance(0.5); collector.run_cycle([printer])                # cycle suivant, réseau stable : l'échec compte
    assert "down" in types(notifier)


def test_manual_mode_counts_every_failure(env):
    """interval_s = 0 (synchronisation manuelle) : les longs écarts entre deux relevés sont normaux, chaque échec compte."""
    collector, storage, client, notifier, clock, printer = env
    collector.cfg.interval_s = 0
    cycle(env, ink(50))
    client.data[printer.host] = SnmpError("timeout")
    for _ in range(3):
        clock.advance(5); collector.run_cycle([printer])               # trois synchronisations espacées de 5 h
    assert "down" in types(notifier)


def test_collector_never_overwrites_a_printer_record_edited_elsewhere(env):
    """La base fait foi : une fiche modifiée depuis le dashboard n'est pas réécrite par la liste passée au collecteur."""
    collector, storage, client, notifier, clock, printer = env
    client.data[printer.host] = [ink(50)]
    collector.run_cycle([printer])
    storage.update_printer(printer.id, name="Nouveau nom", location="Autre bâtiment", user="Awa")
    collector.run_cycle([printer])                                       # liste « périmée » (ancien nom) : sans effet sur la fiche
    row = storage.conn.execute("SELECT name, location, user FROM printers WHERE id=?", (printer.id,)).fetchone()
    assert tuple(row) == ("Nouveau nom", "Autre bâtiment", "Awa")


# --- complément HP (LEDM), relance et reprise -----------------------------------------------------------------
from printwatch.ledm import LedmSupply  # noqa: E402


def hp_env(fetch):
    clock, client, notifier = Clock(), FakeClient(), FakeNotifier()
    storage = Storage(":memory:")
    collector = Collector(storage, client, Thresholds(15, 5, 5), notifier, CollectorConfig(retry_delay_s=0), clock,
                          sleep=lambda s: None, hp_fetch=fetch)
    return collector, storage, client, notifier, clock, Printer("hp", "HP M479", "10.0.0.9", "HP", "M479", "")


def test_hp_unknown_levels_are_completed_and_alert(env_=None):
    calls = []
    def fetch(host):
        calls.append(host)
        return [LedmSupply("cyan", "W2031A", 0, "veryLow", "HP")]
    env = hp_env(fetch)
    cycle(env, ink(-2, "1.2", "Cyan Cartridge HP W2031A"), ink(-2, "1.1", "Black Cartridge HP W2030A"))
    s = {r["key"]: r for r in env[1].conn.execute("SELECT key, pct, alert_level FROM supplies")}
    assert calls == ["10.0.0.9"]
    assert (s["1.2"]["pct"], s["1.2"]["alert_level"]) == (0, 2)       # comblé par l'interface web HP : critique
    assert s["1.1"]["pct"] is None                                    # pas de correspondance : reste inconnu
    assert "critical" in types(env[3])


def test_hp_fetch_failure_or_valid_snmp_keeps_snmp_and_non_hp_never_fetches():
    def boom(host):
        raise RuntimeError("port 80 fermé")
    env = hp_env(boom)
    cycle(env, ink(-2, "1.1", "Black Cartridge HP W2030A"))             # échec LEDM : on garde « inconnu », le cycle réussit
    assert env[1].conn.execute("SELECT pct FROM supplies").fetchone()["pct"] is None

    called = []
    env = hp_env(lambda h: called.append(h) or [])
    cycle(env, ink(50))                                                  # HP mais niveau SNMP valide : aucune requête web
    assert called == []
    collector, storage, client, notifier, clock, printer = env
    epson = Printer("ep", "Epson", "10.0.0.8", "EPSON", "WF", "")
    client.data[epson.host] = [ink(-2)]
    collector.run_cycle([epson])                                         # niveau inconnu mais marque non HP : aucune requête web
    assert called == []


def test_failed_printer_is_retried_once_in_the_same_cycle():
    class Flaky(FakeClient):
        def __init__(self):
            super().__init__(); self.n = 0
        def supplies(self, host):
            self.n += 1
            if self.n == 1:
                raise SnmpError("timeout")
            return [ink(50)]
    clock, client = Clock(), Flaky()
    c = Collector(Storage(":memory:"), client, Thresholds(15, 5, 5), FakeNotifier(), CollectorConfig(retry_delay_s=1), clock, sleep=lambda s: None)
    r = c.run_cycle([Printer("p", "P", "10.0.0.1", "Epson", "WF", "")])
    assert (r.polled, r.failed, client.n) == (1, 0, 2)


def test_failures_after_a_long_interruption_do_not_mark_down(env):
    collector, storage, client, notifier, clock, printer = env
    cycle(env, ink(50))
    client.data[printer.host] = SnmpError("timeout")
    for _ in range(2):
        clock.advance(0.5); collector.run_cycle([printer])             # 2 échecs normaux
    clock.advance(5); collector.run_cycle([printer])                  # reprise après 5 h : 3e échec toléré
    assert "down" not in types(notifier)
    clock.advance(0.5); collector.run_cycle([printer])                # cycle suivant, réseau stable : l'échec compte
    assert "down" in types(notifier)
