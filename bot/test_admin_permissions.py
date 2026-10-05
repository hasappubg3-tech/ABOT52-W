"""Admin permission regressions with in-memory records, no network or DB writes."""
import os
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot.loader import load_bot_symbols

load_bot_symbols()

from bot import data_access as data
from bot import keyboards, callback_handlers as callbacks, message_handlers as messages
from bot import mlz_feature as mlz
from bot.admin_permissions import ADMIN_PERMISSIONS, admin_callback_permission, admin_state_permission


class Rows(list):
    def sort(self, *args):
        return self


class AdminCollection:
    def __init__(self, records):
        self.records = {row["id"]: deepcopy(row) for row in records}

    def find_one(self, query):
        return deepcopy(self.records.get(query["id"]))

    def find(self, *args):
        return Rows(deepcopy(list(self.records.values())))

    def update_one(self, query, change, upsert=False):
        target = query["id"]
        if target in self.records or upsert:
            self.records.setdefault(target, {"id": target}).update(deepcopy(change["$set"]))

    def delete_one(self, query):
        self.records.pop(query["id"], None)


class PermissionFixture:
    def setUp(self):
        self.store = AdminCollection([
            {"id": 900, "permissions": {}},  # owner retains full rights
            {"id": 901},  # unchanged legacy admin
            {"id": 902, "permissions": {"ai_upload": True}},
            {"id": 903, "permissions": {"buttons": True}},
            {"id": 904, "permissions": {}},
            {"id": 905, "permissions": {"stats": True}},
            {"id": 906, "permissions": {"admins": True}},
        ])
        self.file_supervisors = Mock()
        self.file_supervisors.find_one.return_value = None
        self.file_supervisors.find.return_value = Rows()
        self.env = patch.dict(os.environ, {"SUPER_ADMIN_ID": "900"})
        self.db = patch.object(data, "_col", side_effect=lambda name: {
            "admins": self.store, "file_request_admins": self.file_supervisors,
        }[name])
        self.env.start()
        self.db.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.db.stop)


class PermissionTests(PermissionFixture, unittest.TestCase):
    def test_legacy_owner_and_explicit_roles(self):
        for key in ADMIN_PERMISSIONS:
            self.assertTrue(data.has_permission(900, key))
            self.assertTrue(data.has_permission(901, key))
            self.assertFalse(data.has_permission(904, key))
            self.assertFalse(data.has_permission(999, key))
            self.assertEqual(data.has_permission(902, key), key == "ai_upload")

    def test_ai_only_is_an_ordinary_member_except_upload(self):
        self.assertFalse(data.is_admin(902))
        self.assertFalse(data.has_permission(902, "settings_menu"))
        self.assertFalse(data.is_file_supervisor(902))

    def test_button_admin_cannot_access_settings(self):
        self.assertTrue(data.is_admin(903))
        self.assertFalse(data.has_permission(903, "settings_menu"))
        self.assertFalse(data.has_permission(903, "ai_upload"))

    def test_preview_temporarily_denies_all_without_losing_saved_rights(self):
        self.store.records[901]["preview_mode"] = True
        for key in ADMIN_PERMISSIONS:
            self.assertFalse(data.has_permission(901, key))
        self.assertTrue(data.get_admin_permissions(901)["buttons"])

    def test_first_legacy_toggle_preserves_other_capabilities(self):
        data.set_admin_permission(900, 901, "buttons", False)
        for key in ADMIN_PERMISSIONS:
            self.assertEqual(data.has_permission(901, key), key != "buttons")
        data.set_admin_permission(900, 901, "buttons", True)
        self.assertTrue(data.is_admin(901))

    def test_toggle_of_explicit_role_does_not_enable_missing_keys(self):
        data.set_admin_permission(900, 904, "ai_upload", True)
        self.assertEqual(data.get_admin_permissions(904), data.get_admin_permissions(902))

    def test_owner_self_and_unauthorized_grants_are_protected(self):
        for actor, target, key in (
            (901, 900, "buttons"), (901, 901, "buttons"),
            (902, 903, "buttons"), (906, 904, "buttons"),
        ):
            with self.subTest(actor=actor, target=target):
                with self.assertRaises(PermissionError):
                    data.set_admin_permission(actor, target, key, True)
        with self.assertRaises(ValueError):
            data.set_admin_permission(900, 904, "unknown", True)
        with self.assertRaises(ValueError):
            data.set_admin_permission(900, 999, "buttons", True)

    def test_new_admin_cannot_inherit_more_rights_than_creator(self):
        data.add_delegated_admin(906, 907)
        self.assertEqual(data.get_admin_permissions(906), data.get_admin_permissions(907))
        data.add_delegated_admin(900, 902)
        self.assertFalse(data.is_admin(902))  # re-adding does not reset the role

    def test_restore_reserved_for_owner(self):
        self.assertTrue(data.has_permission(900, "owner"))
        self.assertFalse(data.has_permission(901, "owner"))
        self.assertEqual(admin_callback_permission("st_restore"), "owner")
        self.assertEqual(admin_state_permission("wait_restore_zip"), "owner")

    def test_file_requests_are_sent_only_to_authorized_admins(self):
        self.assertEqual({a["user_id"] for a in data.get_authorized_file_admins()}, {900, 901})
        self.file_supervisors.find.return_value = Rows([{"user_id": 902}])
        # Even membership in the supervisor list cannot bypass an explicit admin restriction.
        self.assertNotIn(902, {a["user_id"] for a in data.get_authorized_file_admins()})

    def test_regular_file_supervisors_keep_access(self):
        self.file_supervisors.find_one.return_value = {"user_id": 999}
        self.assertTrue(data.is_file_supervisor(999))
        self.assertFalse(data.is_file_supervisor(902))

    def test_routing_preserves_student_states_and_restricts_admin_states(self):
        for state in ("wait_pom_study_min", "wait_pom_break_min", "wait_comment",
                      "wait_ses_study_time", "wait_file_request", "ai_chat_mode"):
            self.assertIsNone(admin_state_permission(state))
        expected = {
            "wait_admin_id": "admins", "wait_file_admin_id": "admins",
            "wait_label": "buttons", "wait_mlz_new_desc": "buttons",
            "wait_mlz_type": "ai_upload", "wait_api_key_add": "bot_settings",
            "wait_broadcast_msg": "broadcast", "wait_freply_123": "file_supervisor",
        }
        for state, permission in expected.items():
            self.assertEqual(admin_state_permission(state), permission)

    def test_callback_routing(self):
        for value, expected in (
            ("mlz_confirm", "ai_upload"), ("mlz_ed_3", "buttons"),
            ("st_admins", "admins"), ("apt_2_ai_upload", "admins"),
            ("st_broadcast_confirm", "broadcast"), ("st_backup_dl", "backups"),
            ("st_back", "settings_menu"), ("don_thanks_set", "bot_settings"),
            ("exg_add_topic_3", "buttons"), ("st_ai_settings", "bot_settings"),
        ):
            self.assertEqual(admin_callback_permission(value), expected)
        self.assertIsNone(admin_callback_permission("rate_3"))
        self.assertEqual(admin_callback_permission("ci_del_3", admin_section=True), "buttons")


class PermissionKeyboardTests(PermissionFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.patches = [
            patch.object(keyboards, "get_buttons_user", return_value=[
                {"id": 1, "label": "قسم ظاهر", "type": "menu", "new_row": 1}]),
            patch.object(keyboards, "get_buttons", return_value=[]),
            patch.object(keyboards, "_btn_visible_for_user", return_value=True),
            patch.object(keyboards, "_kb_emoji_id", return_value=None),
            patch.object(keyboards, "get_global_caption", return_value=""),
            patch.object(keyboards, "get_caption_buttons", return_value=[]),
            patch.object(keyboards, "get_setting", side_effect=lambda key, default=None: default),
            patch.object(keyboards, "get_library_channel_url", return_value=""),
            patch.object(keyboards, "get_work_mode", return_value=False),
        ]
        for mocked in self.patches:
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_ai_only_keyboard_matches_member(self):
        self.assertEqual(keyboards.build_kb(902).to_dict(), keyboards.build_kb(999).to_dict())

    def test_button_admin_sees_edit_controls_but_not_bot_settings(self):
        rows = keyboards.build_kb(903).keyboard
        text = [button.text for row in rows for button in row]
        self.assertIn(keyboards.BTN_ADD, text)
        self.assertNotIn(keyboards.BTN_SETTINGS, text)

    def test_stats_menu_excludes_unrelated_settings(self):
        markup = keyboards.kb_settings(905)
        self.assertEqual(
            {b.callback_data for row in markup.inline_keyboard for b in row},
            {"st_stats", "st_trending_0"})

    def test_file_supervisor_list_does_not_require_bot_admin_id(self):
        with patch.object(keyboards, "get_file_request_admins", return_value=[
                {"user_id": 999, "username": "supervisor"}]):
            markup = keyboards.kb_file_request_admins(3)
            actions = {b.callback_data for row in markup.inline_keyboard for b in row}
            self.assertIn("fr_admin_del_3_999", actions)

    def test_owner_and_self_permission_menus_have_no_toggles(self):
        for target, actor in ((900, 901), (901, 901)):
            buttons = keyboards.kb_admin_permissions(target, actor).inline_keyboard
            self.assertFalse(any(b.callback_data.startswith("apt_") for row in buttons for b in row))
        markup = keyboards.kb_admin_permissions(902, 900)
        self.assertEqual(len(markup.inline_keyboard), len(ADMIN_PERMISSIONS) + 1)
        self.assertTrue(all(len(b.callback_data.encode()) <= 64
                            for row in markup.inline_keyboard for b in row))

    def test_admin_names_open_permissions_and_owner_cannot_be_deleted(self):
        markup = keyboards.kb_admins_inline()
        actions = {b.callback_data for row in markup.inline_keyboard for b in row}
        self.assertIn("ap_902", actions)
        self.assertNotIn("da_900", actions)

    def test_backup_menu_hides_restore_from_nonowner(self):
        actions = {b.callback_data for row in keyboards.kb_backup_menu(901).inline_keyboard for b in row}
        self.assertIn("st_backup_dl", actions)
        self.assertNotIn("st_restore", actions)


class PermissionHandlerTests(PermissionFixture, unittest.IsolatedAsyncioTestCase):
    def callback(self, uid, payload):
        q = SimpleNamespace(
            from_user=SimpleNamespace(id=uid), data=payload,
            message=SimpleNamespace(chat_id=42, message_id=10, reply_text=AsyncMock()),
            answer=AsyncMock(), edit_message_text=AsyncMock(),
        )
        ctx = SimpleNamespace(user_data={}, bot=SimpleNamespace())
        return q, ctx

    async def test_forbidden_callbacks_never_reach_mutations(self):
        with patch.object(callbacks, "check_rate_limit", return_value=True), \
                patch.object(callbacks, "del_item") as remove, \
                patch.object(callbacks, "set_setting") as change:
            for uid, payload in ((902, "ci_del_3"), (903, "st_work_start"),
                                 (903, "aa"), (902, "exg_add_topic_3"),
                                 (902, "mlz_ed_3"), (901, "st_restore")):
                q, ctx = self.callback(uid, payload)
                await callbacks.cb_manage(SimpleNamespace(callback_query=q), ctx)
                self.assertTrue(q.answer.call_args.kwargs.get("show_alert"))
                self.assertNotIn("state", ctx.user_data)
            remove.assert_not_called()
            change.assert_not_called()

    async def test_ai_only_can_confirm_upload(self):
        q, ctx = self.callback(902, "mlz_confirm")
        with patch.object(callbacks, "check_rate_limit", return_value=True), \
                patch.object(callbacks, "after_mlz_confirm", new_callable=AsyncMock) as confirm:
            await callbacks.cb_manage(SimpleNamespace(callback_query=q), ctx)
            confirm.assert_awaited_once()

    async def test_stats_only_admin_can_view_statistics_without_button_rights(self):
        q, ctx = self.callback(905, "st_stats")
        with patch.object(callbacks, "check_rate_limit", return_value=True), \
                patch.object(callbacks, "get_stats", return_value="احصائيات"):
            await callbacks.cb_manage(SimpleNamespace(callback_query=q), ctx)
            self.assertEqual(q.edit_message_text.call_args.args[0], "احصائيات")

    async def test_permission_menu_and_toggle_work(self):
        q, ctx = self.callback(900, "ap_902")
        await callbacks.cb_manage(SimpleNamespace(callback_query=q), ctx)
        self.assertIn("صلاحيات المشرف", q.edit_message_text.call_args.args[0])
        q.data = "apt_902_buttons"
        await callbacks.cb_manage(SimpleNamespace(callback_query=q), ctx)
        self.assertTrue(data.is_admin(902))
        self.assertTrue(data.has_permission(902, "ai_upload"))
        self.assertFalse(data.has_permission(902, "bot_settings"))

    async def test_owner_delete_and_self_toggle_are_rejected(self):
        for payload in ("da_900", "apt_901_buttons"):
            q, ctx = self.callback(901, payload)
            await callbacks.cb_manage(SimpleNamespace(callback_query=q), ctx)
            q.message.reply_text.assert_awaited_once()
        self.assertTrue(data.is_real_admin(900))
        self.assertTrue(data.is_admin(901))

    async def test_revoked_pending_input_cannot_write(self):
        m = SimpleNamespace(text="أضف شيئاً", chat_id=42, reply_text=AsyncMock())
        update = SimpleNamespace(message=m, effective_user=SimpleNamespace(id=902))
        ctx = SimpleNamespace(user_data={"state": "wait_label"})
        with patch.object(messages, "build_kb", return_value=None), \
                patch.object(messages, "add_btn") as create:
            await messages.on_message(update, ctx)
            create.assert_not_called()
            self.assertNotIn("state", ctx.user_data)
            m.reply_text.assert_awaited_once()

    async def test_ai_only_file_message_starts_upload_flow(self):
        m = SimpleNamespace(text=None, chat_id=42, caption=None, photo=[], video=None,
                            document=SimpleNamespace(file_name="ملزمة.pdf"), audio=None,
                            voice=None, reply_to_message=None, entities=[], caption_entities=[])
        user = SimpleNamespace(id=902, username=None, first_name="مشرف")
        update = SimpleNamespace(message=m, effective_user=user)
        ctx = SimpleNamespace(user_data={}, bot=SimpleNamespace())
        with patch.object(messages, "track_message"), \
                patch.object(messages, "update_user_info"), \
                patch.object(messages, "check_rate_limit", return_value=True), \
                patch.object(messages, "is_file_convo_active", return_value=False), \
                patch.object(messages, "start_mlz_flow", new_callable=AsyncMock, return_value=True) as start:
            await messages.on_message(update, ctx)
            start.assert_awaited_once_with(m, ctx, 902, 42)

    async def test_direct_ai_entry_points_deny_missing_permission(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        ctx = SimpleNamespace(user_data={})
        self.assertFalse(await mlz.start_mlz_flow(message, ctx, 903, 42))
        await mlz.finish_mlz_flow(message, ctx, 903, 42, None)
        message.reply_text.assert_awaited_once()

    async def test_ai_only_can_save_file_without_existing_content_edit_controls(self):
        from bot import content_delivery
        wait = SimpleNamespace(edit_text=AsyncMock())
        ctx = SimpleNamespace(user_data={"mlz_actor_id": 902,
                                        "mlz_label_emojis": {"🔸": "test-emoji-id"}})
        with patch.object(mlz, "add_btn", return_value=20) as create, \
                patch.object(mlz, "add_item") as add_file, \
                patch.object(mlz, "get_storage_channel_id", return_value=None), \
                patch.object(content_delivery, "upload_to_channel", new_callable=AsyncMock, return_value=None):
            await mlz._do_add_mlz(wait, ctx, None, 10, "ملزمة", "document",
                                 "file-1", "الوصف", ["الصف", "المادة"])
            create.assert_called_once()
            add_file.assert_called_once_with(20, "document", "الوصف", "file-1", None, None)
            self.assertIsNone(wait.edit_text.call_args.kwargs["reply_markup"])

    async def test_revocation_during_upload_prevents_final_save(self):
        from bot import content_delivery
        wait = SimpleNamespace(edit_text=AsyncMock())
        ctx = SimpleNamespace(user_data={"mlz_actor_id": 902,
                                        "mlz_label_emojis": {"🔸": "test-emoji-id"}})

        async def revoke(*args):
            self.store.records[902]["permissions"] = {}
            return 100

        with patch.object(mlz, "add_btn", return_value=20), \
                patch.object(mlz, "add_item") as add_file, \
                patch.object(mlz, "del_btn") as undo, \
                patch.object(content_delivery, "upload_to_channel", side_effect=revoke):
            await mlz._do_add_mlz(wait, ctx, None, 10, "ملزمة", "document",
                                 "file-1", "الوصف", ["الصف", "المادة"])
            add_file.assert_not_called()
            undo.assert_called_once_with(20)


if __name__ == "__main__":
    unittest.main()
