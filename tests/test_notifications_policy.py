from __future__ import annotations

import unittest

from src.notifications.policy import DefaultNotificationPolicy, DenyReason, NotificationFacts
from src.notifications.types import NotificationEvent, RecipientKind


class NotificationPolicyTests(unittest.TestCase):
    def test_master_attendance_nudge_requires_pro(self) -> None:
        policy = DefaultNotificationPolicy()
        decision = policy.check(
            NotificationFacts(
                event=NotificationEvent.MASTER_ATTENDANCE_NUDGE,
                recipient=RecipientKind.MASTER,
                chat_id=1,
                plan_is_pro=False,
                master_notify_attendance=True,
            ),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, DenyReason.PRO_REQUIRED)

    def test_master_attendance_nudge_respects_toggle(self) -> None:
        policy = DefaultNotificationPolicy()
        decision = policy.check(
            NotificationFacts(
                event=NotificationEvent.MASTER_ATTENDANCE_NUDGE,
                recipient=RecipientKind.MASTER,
                chat_id=1,
                plan_is_pro=True,
                master_notify_attendance=False,
            ),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, DenyReason.MASTER_ATTENDANCE_DISABLED)

    def test_master_attendance_nudge_allows_when_pro_and_enabled(self) -> None:
        policy = DefaultNotificationPolicy()
        decision = policy.check(
            NotificationFacts(
                event=NotificationEvent.MASTER_ATTENDANCE_NUDGE,
                recipient=RecipientKind.MASTER,
                chat_id=1,
                plan_is_pro=True,
                master_notify_attendance=True,
            ),
        )
        self.assertTrue(decision.allowed)
