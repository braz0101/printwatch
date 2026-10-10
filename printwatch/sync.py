"""Synchronisation à la demande : le dashboard peut réveiller la boucle de collecte pour un cycle immédiat."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator


class SyncController:
    """Pont entre l'API web (qui demande) et la boucle de collecte (qui exécute) dans un même processus.

    Garde-fous : jamais deux cycles en parallèle, et un délai minimal entre deux démarrages (le réseau des imprimantes n'est pas un
    service à marteler : un clic compulsif ne génère pas plus d'un cycle toutes les `min_gap_s` secondes).
    """

    def __init__(self, min_gap_s: float = 15.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.min_gap_s, self._clock = min_gap_s, clock
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self.running = False
        self._last_start = -1e9

    def request(self, queue: bool = False) -> str:
        """`started` : un cycle va démarrer ; `running` : un cycle est déjà en cours ; `too_soon` : le dernier est tout récent ;
        `queued` : (avec `queue=True`) un cycle est en cours et un second suivra aussitôt. Utilisé quand la liste vient de changer
        (ajout, changement d'adresse) : le cycle en cours ne contient pas la nouvelle imprimante."""
        with self._lock:
            if self.running:
                if queue:
                    self._wake.set()
                    return "queued"
                return "running"
            if self._clock() - self._last_start < self.min_gap_s:
                return "too_soon"
            self._wake.set()
            return "started"

    @contextmanager
    def cycle(self) -> Iterator[None]:
        with self._lock:
            self.running, self._last_start = True, self._clock()
        try:
            yield
        finally:
            with self._lock:
                self.running = False

    def wait(self, timeout: float, stop: threading.Event) -> None:
        """Attend l'intervalle normal, une demande de synchronisation ou l'arrêt, selon le premier survenu."""
        end = self._clock() + timeout
        while not stop.is_set() and not self._wake.is_set():
            left = end - self._clock()
            if left <= 0:
                break
            self._wake.wait(min(left, 0.5))
        self._wake.clear()
