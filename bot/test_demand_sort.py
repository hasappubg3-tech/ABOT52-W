"""Demand ordering, material defaults, counters and administrator controls."""
import unittest
from unittest.mock import Mock, patch

from bot.loader import load_bot_symbols

load_bot_symbols()

from bot import data_access as da, keyboards as kb


class DemandSortTests(unittest.TestCase):
    def test_material_and_subject_defaults_across_nested_grades(self):
        materials = {"id": 20, "type": "menu", "label": "الـمـلازم", "parent_id": 19}
        subject = {"id": 21, "type": "menu", "label": "العربي", "parent_id": 20,
                   "sort_alpha": 1}
        with patch.object(da, "get_btn", return_value=materials):
            self.assertFalse(da.menu_sort_by_demand(materials))
            self.assertTrue(da.menu_sort_by_demand(subject))
        self.assertFalse(da.menu_sort_by_demand(dict(subject, sort_demand=0)))
        self.assertFalse(da.menu_sort_by_demand({"type": "compound", "label": "ملازم"}))
        self.assertFalse(da.menu_sort_by_demand(None))

    def test_unrelated_menus_keep_existing_sort(self):
        button = {"id": 5, "type": "menu", "label": "اختبارات", "parent_id": 4}
        with patch.object(da, "get_btn", return_value={"label": "السادس العلمي"}):
            self.assertFalse(da.menu_sort_by_demand(button))
        self.assertFalse(da.menu_sort_by_demand(dict(button, parent_id=None)))

    def test_demand_ranking_is_descending_stable_and_preserves_emoji_fields(self):
        buttons = [
            {"id": 1, "demand_clicks": 2, "new_row": 0},
            {"id": 2, "demand_clicks": 9, "label_emojis": {"📗": "123"}},
            {"id": 3, "demand_clicks": 2},
            {"id": 4},
        ]
        ordered = kb._sort_demand_children(buttons)
        self.assertEqual([b["id"] for b in ordered], [2, 1, 3, 4])
        self.assertEqual([b["new_row"] for b in ordered], [1, 0, 1, 0])
        self.assertEqual(ordered[0]["label_emojis"], {"📗": "123"})
        self.assertEqual(buttons[0]["new_row"], 0)

    def test_student_keyboard_preserves_material_menu_previous_sort(self):
        parent = {"id": 20, "type": "menu", "label": "الملازم", "sort_alpha": 1}
        children = [
            {"id": 21, "type": "menu", "label": "العربي", "demand_clicks": 1, "label_emojis": {}},
            {"id": 22, "type": "menu", "label": "الكيمياء", "demand_clicks": 8, "label_emojis": {}},
        ]
        with patch.object(kb, "is_real_admin", return_value=False), \
                patch.object(kb, "is_admin", return_value=False), \
                patch.object(kb, "get_buttons_user", return_value=children), \
                patch.object(kb, "get_btn", return_value=parent), \
                patch.object(kb, "_btn_visible_for_user", return_value=True), \
                patch.object(kb, "_is_mlazm_subject", return_value=False), \
                patch.object(kb, "get_mlz_parent_emoji_style", return_value={}), \
                patch.object(kb, "has_permission", return_value=False):
            for alpha in (0, 1):
                parent["sort_alpha"] = alpha
                markup = kb.build_kb(500, pid=20)
                labels = [
                    kb._strip_bid_markers(b.text)
                    for row in markup.keyboard for b in row
                ]
                material_labels = [label for label in labels if label in ("العربي", "الكيمياء")]
                self.assertEqual(material_labels, ["العربي", "الكيمياء"])
            parent.update(label="الفيزياء", sort_demand=1, sort_alpha=1)
            markup = kb.build_kb(500, pid=20)
            self.assertEqual(
                [kb._strip_bid_markers(b.text) for b in markup.keyboard[0]],
                ["الكيمياء", "العربي"],
            )

    def test_enable_demand_disables_alpha_and_syncs_twin(self):
        collection = Mock()
        with patch.object(da, "get_btn", return_value={
            "id": 5, "type": "menu", "sort_alpha": 1, "sort_demand": 0
        }), patch.object(da, "_col", return_value=collection), \
                patch.object(da, "get_twin", return_value=6):
            self.assertTrue(da.toggle_sort_demand(5))
        for bid in (5, 6):
            collection.update_one.assert_any_call(
                {"id": bid}, {"$set": {"sort_demand": 1, "sort_alpha": 0}}
            )

    def test_alpha_overrides_demand_setting(self):
        collection = Mock()
        with patch.object(da, "get_btn", return_value={
            "id": 5, "type": "menu", "label": "الكيمياء", "sort_alpha": 1,
            "sort_demand": 1
        }), patch.object(da, "_col", return_value=collection), \
                patch.object(da, "get_twin", return_value=None):
            self.assertTrue(da.toggle_sort_alpha(5))
        collection.update_one.assert_called_once_with(
            {"id": 5}, {"$set": {"sort_alpha": 1, "sort_demand": 0}}
        )

    def test_disabling_demand_does_not_restore_alpha(self):
        collection = Mock()
        with patch.object(da, "get_btn", return_value={
            "id": 5, "type": "menu", "label": "الكيمياء", "sort_demand": 1
        }), patch.object(da, "_col", return_value=collection):
            self.assertFalse(da.toggle_sort_demand(5, _sync=False))
        collection.update_one.assert_called_once_with(
            {"id": 5}, {"$set": {"sort_demand": 0}}
        )

    def test_repeated_student_presses_count_but_admins_do_not(self):
        collection = Mock()
        with patch.object(da, "_col", return_value=collection), \
                patch.object(da, "is_real_admin", return_value=False):
            for _ in range(3):
                da.record_student_button_press({"id": 5}, 101)
        self.assertEqual(collection.update_one.call_count, 3)
        collection.update_one.assert_called_with(
            {"id": 5, "deleted": {"$ne": 1}}, {"$inc": {"demand_clicks": 1}}
        )
        collection.reset_mock()
        with patch.object(da, "_col", return_value=collection), \
                patch.object(da, "is_real_admin", return_value=True):
            da.record_student_button_press({"id": 5}, 101)
        collection.update_one.assert_not_called()

    def test_both_admin_panels_show_new_control_and_inactive_alpha(self):
        button = {"id": 5, "type": "menu", "label": "الكيمياء",
                  "sort_demand": 1, "parent_id": None}
        with patch.object(kb, "get_btn", return_value=button), \
                patch.object(kb, "get_buttons", return_value=[]):
            for panel in (kb.kb_edit_menu_btn, kb.kb_menu_quick):
                markup = panel(5)
                buttons = {b.callback_data: b.text for row in markup.inline_keyboard for b in row}
                self.assertIn("✅", buttons["menu_demand_toggle_5"])
                self.assertIn("⭕", buttons["menu_sort_toggle_5"])


if __name__ == "__main__":
    unittest.main()
