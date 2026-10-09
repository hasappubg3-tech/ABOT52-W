"""Grade discovery after menu reorganisation; no database or Telegram writes."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot.loader import load_bot_symbols

load_bot_symbols()
from bot import mlz_feature as mlz


def button(bid, label, parent=None, kind="menu", **extra):
    return {"id": bid, "label": label, "parent_id": parent, "type": kind, **extra}


def grade_tree():
    return {
        None: [
            button(1, "المرحلة الابتدائية"),
            button(2, "المرحلة المتوسطة"),
            button(3, "السادس العلمي"),
            button(4, "قسم محذوف", deleted=1),
        ],
        1: [button(10, "الأول", 1), button(11, "السادس", 1)],
        2: [button(20, "الأول", 2), button(21, "الثالث المتوسط", 2)],
        3: [button(103, "الملازم", 3)],
        10: [button(110, "الملازم", 10)],
        11: [button(111, "الـملازم", 11)],
        20: [button(120, "الملازم", 20)],
        21: [button(121, "الملازم", 21)],
        111: [button(211, "الرياضيات", 111)],
        120: [button(220, "الرياضيات", 120)],
        211: [button(311, "علي احمد", 211, "compound")],
        220: [button(320, "علي احمد", 220, "compound")],
        4: [button(40, "الخامس الابتدائي", 4)],
        40: [button(140, "الملازم", 40)],
    }


class NestedGradeDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tree = grade_tree()
        self.lookup = patch.object(mlz, "get_buttons",
                                   side_effect=lambda parent=None: self.tree.get(parent, []))
        self.lookup.start()
        self.addCleanup(self.lookup.stop)

    def test_discovers_nested_and_legacy_root_grades_only(self):
        entries = mlz._grade_menu_entries()
        self.assertEqual({entry["button"]["id"] for entry in entries},
                         {10, 11, 20, 21, 3})
        self.assertNotIn(40, {entry["button"]["id"] for entry in entries})

    def test_parent_stage_qualifies_plain_grade_labels(self):
        for query, expected in [
            ("الأول ابتدائي", 10), ("الأول الابتدائي", 10),
            ("السادس ابتدائي", 11), ("الأول متوسط", 20),
            ("الثالث المتوسط", 21), ("السادس علمي", 3),
        ]:
            with self.subTest(query=query):
                self.assertEqual(mlz._find_grade_menu(query)["button"]["id"], expected)

    def test_duplicate_plain_labels_are_not_guessed(self):
        self.assertIsNone(mlz._find_grade_menu("الأول"))
        self.assertEqual(mlz._find_grade_menu("الأول", selected_id=20)["button"]["id"], 20)

    def test_nested_path_uses_original_grade_subject_and_teacher(self):
        with patch.object(mlz, "add_btn") as create:
            result = mlz.find_or_build_mlz_path("السادس ابتدائي", "رياضيات", "علي احمد")
        self.assertEqual([entry["id"] for entry in result], [11, 111, 211, 311])
        self.assertEqual(result[0]["_grade_path_labels"], ["المرحلة الابتدائية", "السادس"])
        create.assert_not_called()

    def test_selected_grade_id_is_used_for_final_path(self):
        result = mlz.find_or_build_mlz_path("الأول", "رياضيات", "علي احمد",
                                          grade_btn_id=20)
        self.assertEqual([entry["id"] for entry in result], [20, 120, 220, 320])

    def test_missing_selected_id_is_not_replaced_with_another_grade(self):
        self.assertIsNone(mlz._find_grade_menu("السادس العلمي", selected_id=999))

    def test_cycle_does_not_loop_forever(self):
        self.tree = {None: [button(9, "أقسام")], 9: [button(9, "أقسام", 9)]}
        self.assertEqual(mlz._grade_menu_entries(), [])

    def test_plain_legacy_root_grade_remains_available_for_manual_choice(self):
        self.tree = {None: [button(9, "الرابع")], 9: [button(90, "الملازم", 9)]}
        self.assertEqual(mlz._find_grade_menu("", selected_id=9)["button"]["id"], 9)


class NestedGradeHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tree = grade_tree()
        self.lookup = patch.object(mlz, "get_buttons",
                                   side_effect=lambda parent=None: self.tree.get(parent, []))
        self.lookup.start()
        self.addCleanup(self.lookup.stop)
        self.q = SimpleNamespace(
            answer=AsyncMock(), get_bot=Mock(return_value=Mock()),
            message=SimpleNamespace(
                chat_id=42, delete=AsyncMock(),
                reply_text=AsyncMock(return_value=SimpleNamespace(message_id=7))),
        )
        self.ctx = SimpleNamespace(user_data={}, bot=Mock())

    async def test_picker_shows_nested_paths_not_section_containers(self):
        await mlz.show_grade_picker(self.q, self.ctx)
        markup = self.q.message.reply_text.call_args.kwargs["reply_markup"]
        options = {b.callback_data: b.text for row in markup.inline_keyboard for b in row}
        self.assertIn("mlz_g_10", options)
        self.assertIn("المرحلة الابتدائية", options["mlz_g_10"])
        self.assertIn("المرحلة المتوسطة", options["mlz_g_20"])
        self.assertNotIn("mlz_g_1", options)
        self.assertNotIn("mlz_g_4", options)

    async def test_picker_does_not_truncate_after_fourteen_grades(self):
        self.tree = {None: [button(i, f"الأول ابتدائي قسم {i}") for i in range(1, 21)]}
        for i in range(1, 21):
            self.tree[i] = [button(100 + i, "الملازم", i)]
        await mlz.show_grade_picker(self.q, self.ctx)
        markup = self.q.message.reply_text.call_args.kwargs["reply_markup"]
        callbacks = {b.callback_data for row in markup.inline_keyboard for b in row}
        self.assertTrue({f"mlz_g_{i}" for i in range(1, 21)} <= callbacks)

    async def test_grade_selection_keeps_id_and_context(self):
        with patch.object(mlz, "_delete_picker", new_callable=AsyncMock), \
                patch.object(mlz, "_refresh_mlz_panel", new_callable=AsyncMock):
            await mlz.after_mlz_grade_pick(self.q, self.ctx, 20)
        self.assertEqual(self.ctx.user_data["mlz_grade_btn_id"], 20)
        self.assertEqual(self.ctx.user_data["mlz_grade"], "المرحلة المتوسطة › الأول")

    async def test_confirmation_resolves_nested_grade_before_final_save(self):
        self.ctx.user_data.update(mlz_grade="السادس ابتدائي", mlz_subject="رياضيات",
                                  mlz_teacher="علي احمد", mlz_year="2027")
        with patch.object(mlz, "finish_mlz_flow", new_callable=AsyncMock) as finish:
            await mlz.after_mlz_confirm(self.q, self.ctx, 10, 42)
        self.assertEqual(self.ctx.user_data["mlz_grade_btn_id"], 11)
        finish.assert_awaited_once()

    async def test_manual_edit_and_cancel_clear_selected_grade_id(self):
        self.ctx.user_data["mlz_grade_btn_id"] = 20
        await mlz.after_mlz_edit_field(self.q, self.ctx, "g_text")
        self.assertNotIn("mlz_grade_btn_id", self.ctx.user_data)
        self.ctx.user_data["mlz_grade_btn_id"] = 20
        mlz._clear_mlz(self.ctx)
        self.assertNotIn("mlz_grade_btn_id", self.ctx.user_data)


class SchoolGradeExtractionTests(unittest.IsolatedAsyncioTestCase):
    def test_extracts_primary_middle_and_secondary_grades(self):
        for phrase in ["الرابع الابتدائي", "الخامس ابتدائي", "السادس الابتدائي",
                       "الأول المتوسط", "الثالث متوسط", "السادس العلمي"]:
            with self.subTest(phrase=phrase):
                info = mlz._extract_info_local(f"كتاب رياضيات للصف {phrase} 2027")
                self.assertEqual(info.get("grade"), phrase)

    def test_unqualified_upper_grade_and_year_are_not_mistaken_for_a_grade(self):
        for text in ["ملزمة رياضيات 2027", "للصف السادس", "للصف الخامس"]:
            with self.subTest(text=text):
                self.assertNotIn("grade", mlz._extract_info_local(text))

    def test_qualified_grade_wins_over_a_part_number(self):
        info = mlz._extract_info_local("ملزمة الجزء الأول للصف السادس الابتدائي 2027")
        self.assertEqual(info["grade"], "السادس الابتدائي")

    async def test_ai_primary_grade_is_not_rejected_as_missing_scientific_branch(self):
        with patch.object(mlz, "get_all_gemini_keys", return_value=["test-only"]), \
                patch.object(mlz, "_call_gemini_text", new_callable=AsyncMock,
                             return_value='{"grade": "السادس الابتدائي"}'):
            info = await mlz.extract_mlz_info("ملزمة رياضيات")
        self.assertEqual(info["grade"], "السادس الابتدائي")


if __name__ == "__main__":
    unittest.main()
