import threading

from printwatch.cli import run_loop
from printwatch.config import ConfigError


class Boom:
    """Collecteur factice : échoue deux fois (CSV illisible, bug), puis réussit, puis demande l'arrêt."""

    class _Storage:                                   # la boucle lit la liste d'imprimantes dans la base
        def count_printers(self): return 1
        def list_printers(self): return []

    def __init__(self, stop):
        self.calls, self.stop, self.storage = 0, stop, self._Storage()

    def run_cycle(self, printers):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("base verrouillée")
        if self.calls == 2:
            self.stop.set()

        class R:
            polled, failed, events, notified, duration_s = 1, 0, 0, 0, 0.0
        return R()


def test_loop_survives_errors_and_keeps_polling(monkeypatch):
    stop = threading.Event()
    boom = Boom(stop)
    seq = iter([ConfigError("liste"), [], []])
    loads = []

    def fake_printers(storage, path):
        loads.append(1)
        assert len(loads) < 10, "la boucle ne s'est pas arrêtée"      # garde-fou : jamais de boucle infinie en test
        v = next(seq)
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr("printwatch.cli._printers_for_cycle", fake_printers)
    run_loop(boom, "p.csv", 0, stop)                 # intervalle 0 : pas d'attente réelle
    assert boom.calls == 2                            # ConfigError : cycle sauté ; RuntimeError : journalisée, la boucle continue


class Counter:
    class _Storage:
        def count_printers(self): return 1
        def list_printers(self): return []

    def __init__(self):
        self.calls, self.storage = 0, self._Storage()

    def run_cycle(self, printers):
        self.calls += 1

        class R:
            polled, failed, events, notified, duration_s = 1, 0, 0, 0, 0.0
        return R()


def test_manual_mode_runs_only_on_sync_request(monkeypatch):
    """interval_s = 0 avec la collecte intégrée : aucun cycle au démarrage ni périodique, un cycle par clic sur Synchroniser."""
    import time

    from printwatch.sync import SyncController
    monkeypatch.setattr("printwatch.cli._printers_for_cycle", lambda storage, path: [])
    stop, ctl, col = threading.Event(), SyncController(min_gap_s=0), Counter()
    t = threading.Thread(target=run_loop, args=(col, None, 0, stop, ctl), daemon=True)
    t.start()
    time.sleep(1.2)
    assert col.calls == 0                             # rien au démarrage, rien tout seul
    assert ctl.request() == "started"
    for _ in range(50):
        if col.calls == 1 and not ctl.running:
            break
        time.sleep(0.05)
    time.sleep(0.8)
    assert col.calls == 1                             # un seul cycle, puis de nouveau en attente
    stop.set()
    t.join(2)
    assert not t.is_alive()
