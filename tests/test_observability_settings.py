import unittest

from src.settings import AppSettings


class ObservabilitySettingsParsingTests(unittest.TestCase):
    def test_parses_alerts_events_list(self):
        settings = AppSettings(
            telegram={"bot_token": "x", "bot_username": "u"},
            database={"postgres_url": "p", "redis_url": "r"},
            observability={"alerts_events": "a.b,c.d"},
        )
        self.assertEqual(settings.observability.alerts_events, {"a.b", "c.d"})

    def test_parses_alert_level_by_event(self):
        settings = AppSettings(
            telegram={"bot_token": "x", "bot_username": "u"},
            database={"postgres_url": "p", "redis_url": "r"},
            observability={"alerts_level_by_event": "a.b=warning,c.d=ERROR"},
        )
        self.assertEqual(settings.observability.alerts_level_by_event["a.b"], "WARNING")
        self.assertEqual(settings.observability.alerts_level_by_event["c.d"], "ERROR")

