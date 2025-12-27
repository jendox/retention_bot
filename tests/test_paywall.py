import unittest


class PaywallUrlTests(unittest.TestCase):
    def test_upgrade_url_from_at_username(self) -> None:
        from src.paywall import upgrade_url_from_contact

        self.assertEqual(upgrade_url_from_contact("@admin"), "https://t.me/admin")

    def test_upgrade_url_from_plain_tme(self) -> None:
        from src.paywall import upgrade_url_from_contact

        self.assertEqual(upgrade_url_from_contact("t.me/admin"), "https://t.me/admin")

    def test_upgrade_url_keeps_https(self) -> None:
        from src.paywall import upgrade_url_from_contact

        self.assertEqual(upgrade_url_from_contact("https://t.me/admin"), "https://t.me/admin")

    def test_upgrade_url_returns_none_for_unknown_contact(self) -> None:
        from src.paywall import upgrade_url_from_contact

        self.assertIsNone(upgrade_url_from_contact("admin"))
