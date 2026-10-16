"""Parent custom-emoji inheritance for teacher buttons inside study materials."""
import unittest
from unittest.mock import patch

from bot.loader import load_bot_symbols

load_bot_symbols()

from bot import data_access, keyboards, message_handlers


STYLE = {"📖": "arabic-subject-emoji"}


class MaterialParentEmojiTests(unittest.TestCase):
    def test_parent_style_is_used_only_inside_material_subjects(self):
        parent = {
            "id": 21, "parent_id": 20, "label": "العربي",
            "label_emojis": STYLE,
        }
        material_menu = {"id": 20, "label": "ملازم"}
        buttons = {21: parent, 20: material_menu}
        with patch.object(keyboards, "get_btn", side_effect=buttons.get):
            self.assertEqual(keyboards.get_mlz_parent_emoji_style(21), STYLE)

        with patch.object(keyboards, "_is_mlazm_subject", return_value=False), \
                patch.object(keyboards, "get_btn") as get_btn:
            self.assertEqual(keyboards.get_mlz_parent_emoji_style(21), {})
            get_btn.assert_not_called()

    def test_legacy_subject_uses_its_saved_custom_emoji_alias(self):
        parent = {"id": 21, "label": "📖العربي"}
        with patch.object(keyboards, "_is_mlazm_subject", return_value=True), \
                patch.object(keyboards, "get_btn", return_value=parent), \
                patch.object(keyboards, "_kb_emoji_style", return_value=STYLE):
            self.assertEqual(keyboards.get_mlz_parent_emoji_style(21), STYLE)

    def test_new_teacher_prefers_parent_emoji_over_sibling_guess(self):
        with patch.object(keyboards, "get_mlz_parent_emoji_style",
                          return_value=STYLE), \
                patch.object(data_access, "get_emoji_similarity_enabled") as enabled, \
                patch.object(data_access, "get_sibling_emoji_hint") as sibling:
            text, style = message_handlers._inherit_new_button_emoji(
                "مدرس العربي", {}, 21, "compound"
            )

        self.assertEqual(text, "مدرس العربي")
        self.assertEqual(style, STYLE)
        enabled.assert_not_called()
        sibling.assert_not_called()

    def test_sibling_emoji_fallback_remains_for_other_buttons(self):
        with patch.object(keyboards, "get_mlz_parent_emoji_style",
                          return_value={}), \
                patch.object(data_access, "get_emoji_similarity_enabled",
                             return_value=True), \
                patch.object(data_access, "get_sibling_emoji_hint",
                             return_value={"_regular": True, "prefix": "⭐", "suffix": ""}):
            text, style = message_handlers._inherit_new_button_emoji(
                "رابط جديد", {}, 21, "menu"
            )

        self.assertEqual(text, "⭐رابط جديد")
        self.assertEqual(style, {})

    def test_existing_teacher_keyboard_inherits_parent_style(self):
        teacher = {
            "id": 45, "type": "compound", "label": "مدرس العربي",
            "label_emojis": {}, "new_row": 1,
        }
        patches = [
            patch.object(keyboards, "is_real_admin", return_value=False),
            patch.object(keyboards, "is_admin", return_value=False),
            patch.object(keyboards, "get_buttons_user", return_value=[teacher]),
            patch.object(keyboards, "get_btn", return_value=None),
            patch.object(keyboards, "_btn_visible_for_user", return_value=True),
            patch.object(keyboards, "_is_mlazm_subject", return_value=False),
            patch.object(keyboards, "get_mlz_parent_emoji_style", return_value=STYLE),
            patch.object(keyboards, "has_permission", return_value=False),
        ]
        for mocked in patches:
            mocked.start()
            self.addCleanup(mocked.stop)

        markup = keyboards.build_kb(500, pid=21)
        rendered = markup.keyboard[0][0].to_dict()
        self.assertEqual(keyboards._strip_bid_markers(rendered["text"]), "مدرس العربي")
        self.assertEqual(rendered["icon_custom_emoji_id"], "arabic-subject-emoji")


if __name__ == "__main__":
    unittest.main()
