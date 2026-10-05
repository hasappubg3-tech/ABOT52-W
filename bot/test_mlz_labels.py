"""Material-title regressions; no live database writes or Telegram requests."""
import unittest
import time
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot.loader import load_bot_symbols

load_bot_symbols()

from bot import data_access as data, mlz_feature as mlz, content_delivery as delivery
from bot import keyboards, callback_handlers as callbacks, message_handlers as messages
from bot.admin_permissions import admin_callback_permission
from bot.mlz_labels import build_mlz_label, legacy_mlz_label_plan

STYLE = {"🔸": "selected-custom-emoji"}


class MaterialLabelTests(unittest.TestCase):
    def test_each_material_type_uses_one_suffix_and_no_pins(self):
        for material_type in mlz.MLZ_TYPES:
            with self.subTest(material_type=material_type):
                self.assertEqual(mlz._build_btn_name(material_type, "2027", STYLE),
                                 f"{material_type} 2027 🔸")

    def test_missing_year_has_no_dangling_space_or_pin(self):
        self.assertEqual(build_mlz_label("ملخص", "", STYLE), "ملخص 🔸")

    def test_parenthesized_year_is_normalized_without_changing_year(self):
        self.assertEqual(build_mlz_label("مراجعة", "(2026)", STYLE), "مراجعة 2026 🔸")

    def test_predecorated_input_does_not_repeat_markers(self):
        self.assertEqual(build_mlz_label("📌ملزمة📌 🔸", "2027", STYLE), "ملزمة 2027 🔸")

    def test_custom_material_types_are_preserved(self):
        self.assertEqual(build_mlz_label("مخطط كلاميات الوراثه", "2026", STYLE),
                         "مخطط كلاميات الوراثه 2026 🔸")

    def test_invalid_or_ambiguous_emoji_style_is_explicitly_rejected(self):
        for style in ({}, None, {"🔸": ""}, {"": "id"}, {"🔸": "one", "⭐": "two"}):
            with self.subTest(style=style):
                with self.assertRaises(ValueError):
                    build_mlz_label("ملزمة", "2027", style)

    def test_reads_the_selected_alias_not_a_hardcoded_icon(self):
        with patch.object(data, "get_setting", return_value="chosen"), \
                patch.object(data, "get_emoji_alias", return_value={
                    "fallback": "⭐", "emoji_id": "another-custom-id"}) as lookup:
            self.assertEqual(data.get_mlz_button_emojis(), {"⭐": "another-custom-id"})
            lookup.assert_called_once_with("chosen")

    def test_missing_selected_alias_does_not_fall_back_to_pins(self):
        with patch.object(data, "get_setting", return_value="removed"), \
                patch.object(data, "get_emoji_alias", return_value=None):
            with self.assertRaises(ValueError):
                data.get_mlz_button_emojis()

    def test_repair_only_changes_wrapped_material_content(self):
        buttons = [
            {"id": 1, "type": "menu", "label": "السادس العلمي", "parent_id": None},
            {"id": 2, "type": "menu", "label": "الـملازم", "parent_id": 1},
            {"id": 3, "type": "menu", "label": "الفيزياء", "parent_id": 2},
            {"id": 4, "type": "compound", "label": "المدرس", "parent_id": 3},
            {"id": 5, "type": "content", "label": "📌ملزمة 2027📌", "parent_id": 4},
            {"id": 6, "type": "content", "label": "ملزمة 2026 🔸", "parent_id": 4},
            {"id": 7, "type": "content", "label": "📌مراجعة (2026)📌", "parent_id": 4},
            {"id": 8, "type": "content", "label": "📌رسالة البداية📌", "parent_id": 1},
            {"id": 9, "type": "menu", "label": "📌قائمة📌", "parent_id": 4},
            {"id": 10, "type": "content", "label": "📌ملزمة 2025📌",
             "parent_id": 4, "deleted": 1},
        ]
        plan = legacy_mlz_label_plan(buttons, STYLE)
        self.assertEqual({row["id"] for row in plan}, {5, 7})
        self.assertEqual(plan[0]["after"], "ملزمة 2027 🔸")
        self.assertEqual(plan[1]["after"], "مراجعة 2026 🔸")
        self.assertEqual(buttons[4]["label"], "📌ملزمة 2027📌")  # pure dry-run

    def test_repair_preserves_unusual_type_and_is_idempotent(self):
        buttons = [
            {"id": 1, "type": "menu", "label": "ملازم", "parent_id": None},
            {"id": 2, "type": "content", "label": "📌مخطط كلاميات الوراثه 2026📌",
             "parent_id": 1},
        ]
        change = legacy_mlz_label_plan(buttons, STYLE)[0]
        self.assertEqual(change["after"], "مخطط كلاميات الوراثه 2026 🔸")
        buttons[1]["label"] = change["after"]
        self.assertEqual(legacy_mlz_label_plan(buttons, STYLE), [])

    def test_repair_does_not_follow_cycles_or_missing_parents(self):
        buttons = [
            {"id": 1, "type": "menu", "label": "قائمة", "parent_id": 2},
            {"id": 2, "type": "menu", "label": "قائمة", "parent_id": 1},
            {"id": 3, "type": "content", "label": "📌ملزمة 2027📌", "parent_id": 1},
            {"id": 4, "type": "content", "label": "📌ملزمة 2027📌", "parent_id": 99},
        ]
        self.assertEqual(legacy_mlz_label_plan(buttons, STYLE), [])

    def test_emoji_selection_button_requires_bot_settings(self):
        markup = keyboards.kb_emoji_alias_detail("9")
        self.assertIn("st_mlz_emoji_9", {
            b.callback_data for row in markup.inline_keyboard for b in row})
        self.assertEqual(admin_callback_permission("st_mlz_emoji_9"), "bot_settings")


class MaterialLabelHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_file_saves_selected_custom_emoji_metadata(self):
        ctx = SimpleNamespace(user_data={"mlz_actor_id": 10, "mlz_label_emojis": STYLE})
        message = SimpleNamespace(edit_text=AsyncMock())
        name = mlz._build_btn_name("وزاريات", "2026", STYLE)
        with patch.object(mlz, "has_permission", return_value=True), \
                patch.object(mlz, "get_mlz_button_emojis", return_value=STYLE), \
                patch.object(mlz, "add_btn", return_value=55) as create, \
                patch.object(mlz, "add_item") as save, \
                patch.object(mlz, "get_storage_channel_id", return_value=None), \
                patch.object(delivery, "upload_to_channel", new_callable=AsyncMock, return_value=None):
            await mlz._do_add_mlz(message, ctx, None, 22, name, "document", "file-1",
                                 "الوصف", ["الصف", name])
            create.assert_called_once_with(22, "content", "وزاريات 2026 🔸", label_emojis=STYLE)
            save.assert_called_once_with(55, "document", "الوصف", "file-1", None, None)

    async def test_cached_pin_wrapped_confirmation_is_reformatted_at_final_save(self):
        ctx = SimpleNamespace(user_data={"mlz_actor_id": 10,
                                        "mlz_label_emojis": {"⭐": "old-emoji"}})
        wait = SimpleNamespace(edit_text=AsyncMock())
        with patch.object(mlz, "has_permission", return_value=True), \
                patch.object(mlz, "get_mlz_button_emojis", return_value=STYLE), \
                patch.object(mlz, "add_btn", return_value=55) as create, \
                patch.object(mlz, "add_item"), \
                patch.object(mlz, "get_storage_channel_id", return_value=None), \
                patch.object(delivery, "upload_to_channel", new_callable=AsyncMock, return_value=None):
            await mlz._do_add_mlz(wait, ctx, None, 22, "📌ملزمة (2027)📌",
                                 "file", "file-1", "", ["الصف", "📌ملزمة (2027)📌"])
            create.assert_called_once_with(22, "content", "ملزمة 2027 🔸", label_emojis=STYLE)
            self.assertIn("ملزمة 2027 🔸", wait.edit_text.call_args.args[0])
            self.assertNotIn("📌", wait.edit_text.call_args.args[0])

    async def test_failed_upload_never_creates_empty_content_button(self):
        ctx = SimpleNamespace(user_data={"mlz_actor_id": 10})
        wait = SimpleNamespace(edit_text=AsyncMock())
        with patch.object(mlz, "has_permission", return_value=True), \
                patch.object(mlz, "get_mlz_button_emojis", return_value=STYLE), \
                patch.object(mlz, "add_btn") as create, \
                patch.object(mlz, "add_item") as save, \
                patch.object(mlz, "get_storage_channel_id", return_value=-100), \
                patch.object(delivery, "upload_to_channel", new_callable=AsyncMock, return_value=None):
            await mlz._do_add_mlz(wait, ctx, None, 22, "ملزمة 2027", "file", "file-1", "", [])
            create.assert_not_called()
            save.assert_not_called()

    async def test_failed_database_save_cleans_new_button_but_not_existing_button(self):
        for existing in (None, 66):
            with self.subTest(existing=existing):
                ctx = SimpleNamespace(user_data={"mlz_actor_id": 10})
                wait = SimpleNamespace(edit_text=AsyncMock())
                with patch.object(mlz, "has_permission", return_value=True), \
                        patch.object(mlz, "get_mlz_button_emojis", return_value=STYLE), \
                        patch.object(mlz, "add_btn", return_value=55), \
                        patch.object(mlz, "add_item", side_effect=RuntimeError("storage failed")), \
                        patch.object(mlz, "del_btn") as cleanup, \
                        patch.object(mlz, "get_storage_channel_id", return_value=None), \
                        patch.object(delivery, "upload_to_channel", new_callable=AsyncMock, return_value=None):
                    await mlz._do_add_mlz(wait, ctx, None, 22, "ملزمة 2027",
                                         "file", "file-1", "", [], existing_bid=existing)
                    if existing is None:
                        cleanup.assert_called_once_with(55)
                    else:
                        cleanup.assert_not_called()

    async def test_missing_emoji_configuration_does_not_create_any_buttons(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        ctx = SimpleNamespace(user_data={"mlz_file_id": "file-1"})
        with patch.object(mlz, "has_permission", return_value=True), \
                patch.object(mlz, "get_mlz_button_emojis", side_effect=ValueError("اختر الإيموجي")), \
                patch.object(mlz, "add_btn") as create:
            await mlz.finish_mlz_flow(message, ctx, 10, 20, None)
            create.assert_not_called()
            message.reply_text.assert_awaited_once()
            self.assertEqual(ctx.user_data["mlz_file_id"], "file-1")

    async def test_selection_saves_alias_for_future_uploads(self):
        q = SimpleNamespace(
            data="st_mlz_emoji_9", from_user=SimpleNamespace(id=10),
            message=SimpleNamespace(chat_id=10, message_id=1),
            answer=AsyncMock(), edit_message_text=AsyncMock(),
        )
        ctx = SimpleNamespace(user_data={})
        with patch.object(callbacks, "has_permission", return_value=True), \
                patch.object(callbacks, "is_admin", return_value=True), \
                patch.object(callbacks, "is_real_admin", return_value=True), \
                patch.object(callbacks, "is_preview_mode", return_value=False), \
                patch.object(callbacks, "get_emoji_alias", return_value={
                    "fallback": "🔸", "emoji_id": "selected-custom-emoji"}), \
                patch.object(callbacks, "set_setting") as setting:
            await callbacks.cb_manage(SimpleNamespace(callback_query=q), ctx)
            setting.assert_called_once_with("mlz_button_emoji_alias", "9")
            q.edit_message_text.assert_awaited_once()


class MaterialButtonPressTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(keyboards, "_emoji_cache",
                                             {"💡": "teacher-emoji", **STYLE}))
        self.stack.enter_context(patch.object(keyboards, "_emoji_cache_ts", time.time()))
        db = Mock()
        db.find_one.return_value = None
        db.find.return_value.sort.return_value = []
        db.count_documents.return_value = 0
        self.stack.enter_context(patch.object(data, "_col", return_value=db))

    def test_legacy_global_teacher_emoji_matches_the_actual_displayed_text(self):
        teacher = {"id": 22, "type": "compound", "label": "💡جاسم الزبيدي"}
        self.assertEqual(keyboards.keyboard_display_label(teacher["label"]), "جاسم الزبيدي")
        self.assertTrue(keyboards.keyboard_label_matches(teacher, "جاسم الزبيدي"))

    def test_explicit_custom_and_plain_emojis_match_their_own_keyboard_text(self):
        self.assertTrue(keyboards.keyboard_label_matches(
            {"label": "ملزمة 2027 🔸", "type": "content", "label_emojis": STYLE}, "ملزمة 2027"))
        self.assertFalse(keyboards.keyboard_label_matches(
            {"label": "💡جاسم الزبيدي", "label_emojis": {}}, "جاسم الزبيدي"))

    def test_old_pinned_keyboard_still_opens_same_id_after_title_repair(self):
        button = {"label": "ملزمة 2027 🔸", "type": "content", "label_emojis": STYLE}
        self.assertTrue(keyboards.keyboard_label_matches(button, "📌ملزمة 2027📌"))
        self.assertFalse(keyboards.keyboard_label_matches(button, "📌ملزمة 2026📌"))
        self.assertFalse(keyboards.keyboard_label_matches(button, "ملزمة أخرى"))

    async def test_channel_upload_updates_are_ignored_without_effective_user(self):
        await messages.on_message(SimpleNamespace(message=None, effective_user=None),
                                  SimpleNamespace(user_data={}))

    async def test_pressing_teacher_or_repaired_material_reaches_file_delivery(self):
        cases = [
            ({"id": 22, "type": "compound", "parent_id": 10, "label": "💡جاسم الزبيدي"},
             "جاسم الزبيدي"),
            ({"id": 55, "type": "content", "parent_id": 22,
              "label": "ملزمة 2027 🔸", "label_emojis": STYLE}, "ملزمة 2027"),
            ({"id": 55, "type": "content", "parent_id": 22,
              "label": "ملزمة 2027 🔸", "label_emojis": STYLE}, "📌ملزمة 2027📌"),
        ]
        from bot.shared import _encode_bid
        from bot import pyro_sender
        for button, display in cases:
            with self.subTest(display=display):
                message = SimpleNamespace(
                    text=display + _encode_bid(button["id"]), chat_id=100,
                    document=None, photo=None, video=None, audio=None, voice=None,
                    reply_text=AsyncMock(), caption=None, sticker=None, reply_to_message=None,
                )
                update = SimpleNamespace(message=message, effective_user=SimpleNamespace(
                    id=100, username=None, first_name="عضو"))
                ctx = SimpleNamespace(user_data={"pid": 10}, bot=Mock())
                with patch.object(messages, "track_message"), \
                        patch.object(messages, "update_user_info"), \
                        patch.object(messages, "is_real_admin", return_value=False), \
                        patch.object(messages, "is_admin", return_value=False), \
                        patch.object(messages, "has_permission", return_value=False), \
                        patch.object(messages, "check_rate_limit", return_value=True), \
                        patch.object(messages, "get_btn", return_value=button), \
                        patch.object(messages, "get_buttons_user", return_value=[
                            {"id": 55, "type": "content", "label": "ملزمة 2027 🔸"}]), \
                        patch.object(messages, "send_items", new_callable=AsyncMock) as send, \
                        patch.object(pyro_sender, "send_animated", new_callable=AsyncMock, return_value=False):
                    await messages.on_message(update, ctx)
                    send.assert_awaited_once_with(message, 55, uid=100, bot=ctx.bot)


if __name__ == "__main__":
    unittest.main()
