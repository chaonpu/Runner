from unittest.mock import Mock
import unittest

from scheduler.production_registry import RegistryGuard
from scheduler.production_watch import watch


class Ticker:
    def __init__(self): self.value = 0
    def now(self): return self.value
    def pause(self, seconds): self.value += seconds


class WatchTests(unittest.TestCase):
    def invoke(self, observer, **kwargs):
        ticker = Ticker()
        result = watch("ports", "same-original-session", "same-env", observe_fn=observer,
                       monotonic=ticker.now, pause=ticker.pause, **kwargs)
        return result, ticker

    def test_foreground_stops_at_success_without_business_verification(self):
        observer = Mock(side_effect=[{"status": s} for s in (
            "dispatched", "running", "awaiting_effect_verification")])
        result, ticker = self.invoke(observer)
        self.assertEqual(ticker.value, 60)
        self.assertEqual(observer.call_count, 3)
        self.assertTrue(all(c.args == ("ports", "same-original-session", "same-env")
                            for c in observer.call_args_list))
        self.assertFalse(result["businessVerified"])
        self.assertFalse(result["safeToRetry"])

    def test_failure_hold_stops_immediately(self):
        observer = Mock(return_value={"status": "hold"})
        result, ticker = self.invoke(observer)
        self.assertEqual((result["status"], ticker.value, observer.call_count), ("hold", 0, 1))

    def test_uncertain_read_or_write_is_never_automatically_repeated(self):
        observer = Mock(side_effect=RegistryGuard("session_port_uncertain_no_retry"))
        with self.assertRaises(RegistryGuard): self.invoke(observer)
        observer.assert_called_once()

    def test_deadline_has_no_cancellation_or_redispatch(self):
        observer = Mock(return_value={"status": "running"})
        with self.assertRaisesRegex(RegistryGuard, "deadline_manual_review"):
            self.invoke(observer, deadline_seconds=60)
        self.assertEqual(observer.call_count, 2)

    def test_invalid_bounds_never_observe(self):
        for kwargs in ({"interval": 1}, {"interval": True}, {"deadline_seconds": 99999}):
            observer = Mock()
            with self.assertRaises(RegistryGuard): self.invoke(observer, **kwargs)
            observer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
