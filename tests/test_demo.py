import os

import pytest

from printwatch.cli import _prepare_demo
from printwatch.collector import Collector
from printwatch.config import AppConfig, CollectorConfig, Thresholds
from printwatch.demo import FLEET, DemoClient
from printwatch.notify import make_notifier
from printwatch.snmp import SnmpError
from printwatch.storage import Storage


class Clock:
    def __init__(self): self.t = 1_800_000_000.0
    def __call__(self): return self.t


def test_demo_fleet_levels_drift_and_unreachable_hosts_fail():
    clock = Clock()
    c = DemoClient(clock)
    first = {r.index: r.level for r in c.supplies("192.0.2.14")}
    clock.t += 600                                                      # 10 minutes de démonstration
    later = {r.index: r.level for r in c.supplies("192.0.2.14")}
    assert all(later[k] <= first[k] for k in first) and later != first    # les niveaux baissent
    with pytest.raises(SnmpError):
        c.supplies("192.0.2.20")                                          # imprimante « éteinte » du parc de démo
    with pytest.raises(SnmpError):
        c.supplies("10.9.1.1")                                            # hôte ajouté à la main et injoignable
    assert c.supplies("10.20.30.40") == c.supplies("10.20.30.40")         # hôte inconnu : relevés synthétiques déterministes


def test_demo_cycle_with_hp_completion_and_no_real_names():
    clock, storage = Clock(), Storage(":memory:")
    client = DemoClient(clock)
    col = Collector(storage, client, Thresholds(15, 5, 5), make_notifier(AppConfig().smtp), CollectorConfig(retry_delay_s=0), clock,
                    sleep=lambda s: None, hp_fetch=client.ledm)
    r = col.run_cycle([p for p, _ in FLEET])
    assert (r.polled, r.failed) == (len(FLEET), 2)
    hp = {row["key"]: row["pct"] for row in storage.conn.execute("SELECT key, pct FROM supplies WHERE printer_id='demo-18'")}
    assert hp["1.1"] == 0 and hp["1.2"] == 0 and hp["1.4"] == 0 and hp["1.3"] > 0     # -2 SNMP comblés par l'« interface web » HP simulée
    assert not any(n in p.user for p, _ in FLEET for n in ("NOM1", "NOM2", "NOM3", "NOM4"))  # aucun nom du parc réel dans la démo


def test_prepare_demo_creates_a_throwaway_db_without_touching_others(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    prod = tmp_path / "printwatch.db"
    prod.write_text("production")
    cfg = AppConfig()
    _prepare_demo(cfg)
    assert cfg.collector.db_path == "demo.db" and cfg.collector.interval_s == 60
    st = Storage("demo.db")
    nb = len(st.list_printers())
    st.close()   # Windows : un fichier SQLite ouvert ne peut pas être supprimé par la relance ci-dessous
    assert prod.read_text() == "production" and nb == len(FLEET)
    _prepare_demo(AppConfig())                                            # relance : repart de zéro, sans erreur
    assert os.path.exists("demo.db")
