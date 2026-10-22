from datetime import datetime,timezone
import unittest
from scheduler.calendar_runtime import current,day_rule,adjacent
from scheduler.exchange_schedule import evaluate

class UnifiedCalendarTests(unittest.TestCase):
    def test_us_future_and_a_share_unknown_are_independent(self):
        us=evaluate('stock_us',now=datetime(2027,11,26,18,tzinfo=timezone.utc))
        self.assertTrue(us['scheduledSessionClosed'])
        self.assertIn('13:00:00',us['scheduledClose'])
        self.assertFalse(evaluate('stock_a',now=datetime(2027,11,26,8,tzinfo=timezone.utc))['calendarCoverageCurrent'])
    def test_runtime_provenance_is_in_every_date_judgment(self):
        result=evaluate('stock_us',now=datetime(2026,10,22,23,tzinfo=timezone.utc))
        self.assertEqual(result['calendarRevision'],current()['revision'])
        self.assertFalse(result['marketDataReady']);self.assertFalse(result['businessDateAuthorized'])

if __name__=='__main__':unittest.main()
