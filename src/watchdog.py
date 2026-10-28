"""Watchdog: ana async döngü takılırsa botu öldürür, Railway ON_FAILURE ile yeniden başlatır."""
import os
import threading
import time

from loguru import logger


class Watchdog:
    """Başlatıldığında arka plan thread'i açar. `kick()` her tur sonunda çağrılmalı.
    `timeout_seconds` içinde kick gelmezse process'i öldürür."""

    def __init__(self, timeout_seconds: float):
        self._timeout = timeout_seconds
        self._last_kick = time.monotonic()
        self._stopped = False

    def kick(self) -> None:
        self._last_kick = time.monotonic()

    def start(self) -> None:
        t = threading.Thread(target=self._run, daemon=True, name="watchdog")
        t.start()
        logger.info(f"Watchdog started: {self._timeout}s timeout")

    def stop(self) -> None:
        self._stopped = True

    def _run(self) -> None:
        while not self._stopped:
            elapsed = time.monotonic() - self._last_kick
            if elapsed >= self._timeout:
                logger.critical(f"Watchdog timeout: no cycle progress for {elapsed:.0f}s. Killing process.")
                os._exit(1)
            time.sleep(30)
