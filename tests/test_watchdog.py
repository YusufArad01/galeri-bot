"""Watchdog testleri: kick gelirse yaşar, gelmezse os._exit çağırır."""
import time
import unittest
from unittest.mock import patch

from src.watchdog import Watchdog


class WatchdogTests(unittest.TestCase):
    def test_kick_resets_timer(self):
        wd = Watchdog(timeout_seconds=10)
        time.sleep(0.05)
        wd.kick()
        # kick sonrası elapsed sıfırlanmalı
        self.assertLess(time.monotonic() - wd._last_kick, 0.05)

    def test_fires_when_timeout_exceeded(self):
        wd = Watchdog(timeout_seconds=0.01)
        # _run'ın bir turunu elle çalıştır: sleep(30) yüzünden gerçek thread'i kullanmıyoruz
        wd._last_kick = time.monotonic() - 1.0
        with patch("os._exit") as mock_exit, patch("time.sleep"):
            # _run sonsuz; _stopped'u mock_exit sonrası True yapalım
            def stop_after_exit(_code):
                wd._stopped = True
            mock_exit.side_effect = stop_after_exit
            wd._run()
            mock_exit.assert_called_once_with(1)

    def test_does_not_fire_when_kicked(self):
        wd = Watchdog(timeout_seconds=10)
        wd.kick()
        with patch("os._exit") as mock_exit, patch("time.sleep") as mock_sleep:
            # bir iterasyondan sonra dur
            mock_sleep.side_effect = lambda _s: setattr(wd, "_stopped", True)
            wd._run()
            mock_exit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
