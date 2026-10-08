import unittest
from unittest.mock import patch

from bot.loader import load_bot_symbols

load_bot_symbols()

from bot import keyboards
from bot.admin_permissions import admin_callback_permission, admin_state_permission
from bot.shared import MLZ_FILTER_BUTTON_MARKER_ID, _decode_bid


class MlzFilterButtonConfigTests(unittest.TestCase):
    def _build_admin_markup(self, settings):
        with (
            patch.object(keyboards, "get_buttons", return_value=[]),
            patch.object(keyboards, "get_buttons_user", return_value=[]),
            patch.object(keyboards, "get_btn", return_value=None),
            patch.object(keyboards, "is_admin", return_value=True),
            patch.object(keyboards, "is_real_admin", return_value=True),
            patch.object(
                keyboards,
                "has_permission",
                side_effect=lambda uid, permission: permission == "buttons",
            ),
            patch.object(keyboards, "_is_mlazm_subject", return_value=True),
            patch.object(
                keyboards,
                "get_setting",
                side_effect=lambda key, default=None: settings.get(key, default),
            ),
            patch.object(keyboards, "_kb_emoji_id", return_value=None),
        ):
            return keyboards.build_kb(12345, pid=99)

    def test_admin_sees_filter_button_with_custom_name_and_emoji(self):
        markup = self._build_admin_markup({
            "mlz_filter_button_label": "🪄 فلترة الملازم",
            "mlz_filter_button_label_emojis": {"🪄": "custom-emoji-id"},
        })
        button = markup.keyboard[0][0]
        visible_text, marker = _decode_bid(button.text)

        self.assertEqual(visible_text, "فلترة الملازم")
        self.assertEqual(marker, MLZ_FILTER_BUTTON_MARKER_ID)
        self.assertEqual(button.to_dict()["icon_custom_emoji_id"], "custom-emoji-id")

    def test_filter_press_recognizes_current_legacy_and_stable_marker(self):
        with patch.object(
            keyboards,
            "get_setting",
            side_effect=lambda key, default=None: {
                "mlz_filter_button_label": "🪄 فلترة الملازم",
                "mlz_filter_button_label_emojis": {"🪄": "custom-emoji-id"},
            }.get(key, default),
        ):
            self.assertTrue(keyboards.is_mlz_filter_button_press("فلترة الملازم"))
            self.assertTrue(keyboards.is_mlz_filter_button_press("🔍 فلتر البحث"))
            self.assertTrue(keyboards.is_mlz_filter_button_press("قديم", marker_bid=MLZ_FILTER_BUTTON_MARKER_ID))

    def test_rename_controls_require_button_management_permission(self):
        self.assertEqual(admin_callback_permission("mfl_rename"), "buttons")
        self.assertEqual(admin_state_permission("wait_filter_button_label"), "buttons")

    def test_rename_saves_label_and_custom_emoji_map(self):
        with patch.object(keyboards, "set_setting") as save:
            keyboards.set_mlz_filter_button_config(
                "🪄 فلترة الملازم",
                {"🪄": "custom-emoji-id"},
            )

        self.assertEqual(
            [call.args for call in save.call_args_list],
            [
                ("mlz_filter_button_label", "🪄 فلترة الملازم"),
                ("mlz_filter_button_label_emojis", {"🪄": "custom-emoji-id"}),
            ],
        )


if __name__ == "__main__":
    unittest.main()
