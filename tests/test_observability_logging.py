import json
import logging
import unittest
from unittest.mock import AsyncMock, patch

from src.observability.alerts import AdminAlerter
from src.observability.context import reset_log_context, set_log_context
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
        bot = AsyncMock()
        alerter = AdminAlerter(bot=bot, admin_ids={1}, redis=None, default_throttle_sec=60)
        with patch("src.observability.alerts.notify_admins", new=AsyncMock()) as mocked_notify:
            first = await alerter.notify(event="bot.unhandled_exception", text="boom", throttle_key="k1")
            second = await alerter.notify(event="bot.unhandled_exception", text="boom", throttle_key="k1")
            third = await alerter.notify(event="bot.unhandled_exception", text="boom", throttle_key="k2")

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertTrue(third)
        self.assertEqual(mocked_notify.await_count, 2)

