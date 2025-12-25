import json
import logging
import unittest
from unittest.mock import AsyncMock, patch

from src.settings import AppSettings, app_settings
from src.observability.alerts import AdminAlerter
from src.observability.context import reset_log_context, set_log_context
from src.observability.events import EventLogger
from src.observability.logging import JsonFormatter


class JsonFormatterTests(unittest.TestCase):
    def test_includes_context_and_extras(self):
        formatter = JsonFormatter(service="svc", env="test", version="1.2.3")
        token = set_log_context({"trace_id": "t1", "update_id": 1})
        try:
            logger = logging.getLogger("test")
            record = logger.makeRecord(
                name="test",
                level=logging.INFO,
                fn=__file__,
                lno=10,
                msg="master_reg.start_result",
                args=(),
                exc_info=None,
                func=None,
                extra={"foo": "bar", "update_id": 2},
            )
            payload = json.loads(formatter.format(record))
        finally:
            reset_log_context(token)

        self.assertEqual(payload["service"], "svc")
        self.assertEqual(payload["env"], "test")
        self.assertEqual(payload["version"], "1.2.3")
        self.assertEqual(payload["event"], "master_reg.start_result")
        self.assertEqual(payload["trace_id"], "t1")
        self.assertEqual(payload["update_id"], 1)
        self.assertEqual(payload["foo"], "bar")
        self.assertEqual(payload["extra_update_id"], 2)


class AdminAlerterTests(unittest.IsolatedAsyncioTestCase):
    async def test_throttles_same_key_in_memory(self):
        settings = AppSettings(
            telegram={"bot_token": "x", "bot_username": "u"},
            database={"postgres_url": "p", "redis_url": "r"},
        )
        token = app_settings.set(settings)
        bot = AsyncMock()
        alerter = AdminAlerter(bot=bot, admin_ids={1}, redis=None, default_throttle_sec=60)
        try:
            with patch("src.observability.alerts.notify_admins", new=AsyncMock()) as mocked_notify:
                first = await alerter.notify(event="bot.unhandled_exception", text="boom", throttle_key="k1")
                second = await alerter.notify(event="bot.unhandled_exception", text="boom", throttle_key="k1")
                third = await alerter.notify(event="bot.unhandled_exception", text="boom", throttle_key="k2")
        finally:
            app_settings.reset(token)

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertTrue(third)
        self.assertEqual(mocked_notify.await_count, 2)


class EventLoggerTests(unittest.IsolatedAsyncioTestCase):
    async def test_aexception_triggers_alert_policy(self):
        settings = AppSettings(
            telegram={"bot_token": "x", "bot_username": "u"},
            database={"postgres_url": "p", "redis_url": "r"},
        )
        token = app_settings.set(settings)
        try:
            ev = EventLogger("tests.event_logger")
            admin_alerter = AsyncMock()

            try:
                raise RuntimeError("boom")
            except Exception as exc:
                await ev.aexception(
                    "master_reg.complete_failed",
                    exc=exc,
                    stage="use_case",
                    admin_alerter=admin_alerter,
                )

            admin_alerter.notify.assert_awaited()
            kwargs = admin_alerter.notify.await_args.kwargs
            self.assertEqual(kwargs["event"], "master_reg.complete_failed")
            self.assertEqual(kwargs["level"], "ERROR")
            self.assertIn("master_reg.complete_failed:use_case:RuntimeError", kwargs["throttle_key"])
        finally:
            app_settings.reset(token)

    async def test_sampling_can_drop_event(self):
        settings = AppSettings(
            telegram={"bot_token": "x", "bot_username": "u"},
            database={"postgres_url": "p", "redis_url": "r"},
            observability={"log_sample_rate_by_event": {"noisy.event": 0.0}},
        )
        token = app_settings.set(settings)
        try:
            ev = EventLogger("tests.event_logger_sampling")
            with patch.object(ev._logger, "info") as mocked_info:
                ev.info("noisy.event", foo="bar")
                ev.info("other.event", foo="bar")
            self.assertEqual(mocked_info.call_count, 1)
            self.assertEqual(mocked_info.call_args.args[0], "other.event")
        finally:
            app_settings.reset(token)

    async def test_allowlist_blocks_alerts(self):
        settings = AppSettings(
            telegram={"bot_token": "x", "bot_username": "u"},
            database={"postgres_url": "p", "redis_url": "r"},
            observability={"alerts_events": {"some.other.event"}},
        )
        token = app_settings.set(settings)
        try:
            ev = EventLogger("tests.event_logger_allowlist")
            admin_alerter = AsyncMock()
            try:
                raise RuntimeError("boom")
            except Exception as exc:
                await ev.aexception(
                    "master_reg.complete_failed",
                    exc=exc,
                    stage="use_case",
                    admin_alerter=admin_alerter,
                )
            admin_alerter.notify.assert_not_awaited()
        finally:
            app_settings.reset(token)

    async def test_allowlist_can_enable_new_event(self):
        settings = AppSettings(
            telegram={"bot_token": "x", "bot_username": "u"},
            database={"postgres_url": "p", "redis_url": "r"},
            observability={
                "alerts_events": {"custom.event"},
                "alerts_level_by_event": {"custom.event": "WARNING"},
                "alerts_text_by_event": {"custom.event": "Custom human text"},
            },
        )
        token = app_settings.set(settings)
        try:
            ev = EventLogger("tests.event_logger_new_event")
            admin_alerter = AsyncMock()
            await ev.aerror("custom.event", admin_alerter=admin_alerter, foo="bar")
            admin_alerter.notify.assert_awaited()
            kwargs = admin_alerter.notify.await_args.kwargs
            self.assertEqual(kwargs["event"], "custom.event")
            self.assertEqual(kwargs["level"], "WARNING")
            self.assertEqual(kwargs["text"], "Custom human text")
        finally:
            app_settings.reset(token)
