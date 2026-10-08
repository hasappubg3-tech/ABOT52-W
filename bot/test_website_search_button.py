import unittest
from unittest.mock import patch

from bot.loader import load_bot_symbols

load_bot_symbols()

from bot import keyboards


class WebsiteSearchButtonTests(unittest.TestCase):
    def _build_markup(self, button_id):
        button = {
            "id": button_id,
            "type": "special",
            "label": "بحث الموقع",
            "label_emojis": {},
            "parent_id": None,
        }
        with (
            patch.object(keyboards, "get_buttons_user", return_value=[button]),
            patch.object(keyboards, "get_btn", return_value=None),
            patch.object(keyboards, "is_admin", return_value=False),
            patch.object(keyboards, "is_real_admin", return_value=False),
            patch.object(keyboards, "has_permission", return_value=False),
            patch.object(keyboards, "_is_mlazm_subject", return_value=False),
        ):
            return keyboards.build_kb(12345)

    def _build_keyboard(self, button_id):
        return self._build_markup(button_id).keyboard[0][0]

    def test_control_keyboard_requests_persistent_display(self):
        markup = self._build_markup(14434)
        self.assertTrue(markup.is_persistent)
        self.assertTrue(markup.to_dict()["is_persistent"])

    def test_search_button_opens_the_website_mini_app(self):
        button = self._build_keyboard(14433)
        self.assertEqual(
            button.web_app.url,
            "https://alameer-iq.com/?open_search=1",
        )
        self.assertEqual(button.text, "بحث الموقع")

    def test_other_keyboard_buttons_keep_their_existing_message_routing(self):
        button = self._build_keyboard(14434)
        self.assertIsNone(button.web_app)
        self.assertTrue(button.text.endswith(keyboards._encode_bid(14434)))


if __name__ == "__main__":
    unittest.main()
