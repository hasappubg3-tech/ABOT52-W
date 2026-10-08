"""Website-only material visibility controls and twin synchronization."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot.loader import load_bot_symbols

load_bot_symbols()

from bot import callback_handlers, data_access as data, keyboards


class WebsiteVisibilityDataAccessTests(unittest.TestCase):
    def test_hiding_an_item_updates_its_twin_and_refreshes_site_search(self):
        item_records = {
            11: {"id": 11, "type": "file", "file_id": "first"},
            22: {"id": 22, "type": "file", "file_id": "second"},
        }
        items = Mock()
        items.find_one.side_effect = lambda query: item_records.get(query["id"])
        twins = Mock()
        twins.find_one.return_value = {"a": 11, "b": 22}
        index_state = Mock()

        def collection(name):
            return {
                "content_items": items,
                "item_twins": twins,
                "website_index_state": index_state,
            }[name]

        with patch.object(data, "_col", side_effect=collection):
            self.assertTrue(data.set_item_website_hidden(11, True))

        self.assertEqual(items.update_one.call_count, 2)
        self.assertEqual(
            items.update_one.call_args_list[0].args,
            ({"id": 11}, {"$set": {"website_hidden": True}}),
        )
        self.assertEqual(
            items.update_one.call_args_list[1].args,
            ({"id": 22}, {"$set": {"website_hidden": True}}),
        )
        index_state.update_one.assert_called_once_with(
            {"_id": "content_visibility"},
            {"$inc": {"revision": 1}},
            upsert=True,
        )

    def test_unhiding_removes_the_visibility_flag(self):
        items = Mock()
        items.find_one.return_value = {
            "id": 11, "type": "file", "file_id": "first",
            "website_hidden": True,
        }
        twins = Mock()
        twins.find_one.return_value = None
        collections = {
            "content_items": items,
            "item_twins": twins,
            "website_index_state": Mock(),
        }

        with patch.object(data, "_col", side_effect=collections.__getitem__):
            self.assertTrue(data.set_item_website_hidden(11, False))

        items.update_one.assert_called_once_with(
            {"id": 11}, {"$unset": {"website_hidden": ""}}
        )

    def test_item_actions_offer_hide_and_restore_for_website_files(self):
        with patch.object(keyboards, "get_item", return_value={
            "id": 11, "type": "file", "file_id": "first",
        }):
            visible_markup = keyboards.kb_item_actions(11)
        self.assertEqual(
            visible_markup.inline_keyboard[1][0].callback_data,
            "ci_website_toggle_11",
        )
        self.assertIn(
            "إخفاء الملزمة من الموقع",
            visible_markup.inline_keyboard[1][0].text,
        )

        with patch.object(keyboards, "get_item", return_value={
            "id": 11, "type": "file", "file_id": "first",
            "website_hidden": True,
        }):
            hidden_markup = keyboards.kb_item_actions(11)
        self.assertIn(
            "إظهار الملزمة في الموقع",
            hidden_markup.inline_keyboard[1][0].text,
        )

        with patch.object(keyboards, "get_item", return_value={
            "id": 12, "type": "text", "content": "وصف",
        }):
            text_markup = keyboards.kb_item_actions(12)
        self.assertEqual(len(text_markup.inline_keyboard), 1)


class WebsiteVisibilityCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_toggle_saves_flag_and_refreshes_item_controls(self):
        query = SimpleNamespace(
            data="ci_website_toggle_11",
            from_user=SimpleNamespace(id=903),
            message=SimpleNamespace(chat_id=44, message_id=55),
            edit_message_reply_markup=AsyncMock(),
            answer=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(user_data={})

        with patch.object(callback_handlers, "has_permission", return_value=True), \
                patch.object(callback_handlers, "is_admin", return_value=True), \
                patch.object(callback_handlers, "is_real_admin", return_value=True), \
                patch.object(callback_handlers, "is_preview_mode", return_value=False), \
                patch.object(callback_handlers, "get_item", return_value={
                    "id": 11, "type": "file", "file_id": "first",
                }), \
                patch.object(callback_handlers, "set_item_website_hidden") as save, \
                patch.object(callback_handlers, "kb_item_actions", return_value="updated"):
            save.return_value = True
            await callback_handlers.cb_manage(update, context)

        save.assert_called_once_with(11, True)
        query.edit_message_reply_markup.assert_awaited_once_with(
            reply_markup="updated"
        )
        self.assertEqual(query.answer.await_count, 2)
