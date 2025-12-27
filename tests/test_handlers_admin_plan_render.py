import unittest
from types import SimpleNamespace


class AdminPlanRenderTests(unittest.TestCase):
    def test_render_plan_text_free_uses_limits(self) -> None:
        from src.handlers import admin as h

        plan = SimpleNamespace(is_pro=False, source="free", active_until=None)
        usage = SimpleNamespace(clients_count=1, bookings_created_this_month=2)
        text = h._render_plan_text(title="T", plan=plan, usage=usage, horizon_days=7)

        self.assertIn("T", text)
        self.assertNotIn("∞", text)

    def test_render_plan_text_pro_uses_infinity(self) -> None:
        from src.handlers import admin as h

        plan = SimpleNamespace(is_pro=True, source="paid", active_until=None)
        usage = SimpleNamespace(clients_count=1, bookings_created_this_month=2)
        text = h._render_plan_text(title="T", plan=plan, usage=usage, horizon_days=60)

        self.assertIn("∞", text)
