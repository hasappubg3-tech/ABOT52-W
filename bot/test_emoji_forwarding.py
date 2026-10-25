import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram import Message, Update
from telegram.ext import ExtBot

from bot import emoji_customization as custom
from bot.emoji_forwarding import ForwardOriginFilter, handle_forwarded_emoji_message
from bot.loader import load_bot_symbols
from bot.test_emoji_customization import FakeBot, FakeQuery, MemoryOverrides

load_bot_symbols()
from bot import message_handlers


def forwarded_message(owner=123, source_bot=888, text="⭐ تقييم الملف: 0\n👥 عدد التقييمات: 0",
                      caption=None):
    return SimpleNamespace(
        text=text, entities=[], caption=caption, caption_entities=[],
        chat_id=owner, chat=SimpleNamespace(id=owner, type="private"),
        message_id=900, forward_origin=SimpleNamespace(
            sender_user=SimpleNamespace(id=source_bot, is_bot=True),
        ),
    )


class EmojiForwardingTests(unittest.TestCase):
    def test_modern_forward_metadata_from_real_telegram_deserialization(self):
        async def run_check():
            bot = FakeBot()
            bot.id = 888
            message = Message.de_json({
                "message_id": 900, "date": 1792923000,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 123, "is_bot": False, "first_name": "Owner"},
                "text": "⭐ تقييم الملف: 0\n👥 عدد التقييمات: 0",
                "forward_origin": {
                    "type": "user", "date": 1792922000,
                    "sender_user": {"id": 888, "is_bot": True, "first_name": "Test Bot"},
                },
            }, bot)
            self.assertTrue(ForwardOriginFilter().filter(message))
            data = {"state": "wait_edit_label"}
            update = Update(update_id=1, message=message)
            context = SimpleNamespace(bot=bot, user_data=data)
            self.assertTrue(ForwardOriginFilter().check_update(update))
            with patch.dict(os.environ, {"SUPER_ADMIN_ID": "123"}), \
                    patch.object(custom, "_load_overrides", return_value={}), \
                    patch.object(message_handlers, "has_permission", return_value=False) as permission:
                await message_handlers.on_message(update, context)
                permission.assert_not_called()
            self.assertEqual(data["bot_emoji_customization"]["target"]["text"], message.text)
        asyncio.run(run_check())

    def test_modern_nontext_forward_passes_filter_and_foreign_bot_is_ignored(self):
        async def run_check():
            bot = SimpleNamespace(id=888, send_message=AsyncMock())
            message = Message.de_json({
                "message_id": 900, "date": 1792923000,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 123, "is_bot": False, "first_name": "Owner"},
                "caption": "📚 وصف الملزمة",
                "forward_origin": {
                    "type": "user", "date": 1792922000,
                    "sender_user": {"id": 999, "is_bot": True, "first_name": "Other Bot"},
                },
            }, None)
            self.assertTrue(ForwardOriginFilter().filter(message))
            with patch.dict(os.environ, {"SUPER_ADMIN_ID": "123"}):
                result = await handle_forwarded_emoji_message(
                    message, bot, 123, 123, message.chat.type, {},
                )
            self.assertFalse(result)
            bot.send_message.assert_not_called()
        asyncio.run(run_check())

    def test_forwarding_opens_editor_before_preview_permission_checks_and_saves(self):
        async def run_flow():
            bot = FakeBot()
            bot.id = 888
            store = MemoryOverrides()
            message = forwarded_message()
            context = SimpleNamespace(bot=bot, user_data={"state": "wait_edit_label"})
            update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=123))
            with patch.dict(os.environ, {"SUPER_ADMIN_ID": "123"}), \
                    patch.object(custom, "_collection", return_value=store), \
                    patch.object(custom, "_OVERRIDE_CACHE", {"expires": 0.0, "values": {}}), \
                    patch.object(message_handlers, "is_admin", return_value=False), \
                    patch.object(message_handlers, "has_permission", return_value=False) as permission:
                await message_handlers.on_message(update, context)
                permission.assert_not_called()
                state = context.user_data["bot_emoji_customization"]
                self.assertEqual(state["target"]["text"], message.text)
                self.assertFalse(state["target"]["editable"])
                picker = bot.messages[(123, bot.next_id - 1)]
                select = picker.reply_markup.inline_keyboard[0][0].callback_data
                await custom.handle_owner_emoji_callback(SimpleNamespace(
                    callback_query=FakeQuery(bot, select, 123, picker),
                ), context)
                await custom.handle_owner_emoji_input(
                    SimpleNamespace(text="🔥", entities=[]),
                    bot, 123, 123, "private", context.user_data,
                )
                preview = bot.messages[(123, bot.next_id - 1)]
                save = preview.reply_markup.inline_keyboard[-1][0].callback_data
                query = FakeQuery(bot, save, 123, preview)
                await custom.handle_owner_emoji_callback(
                    SimpleNamespace(callback_query=query), context,
                )
                # The forwarded copy is an incoming owner message and cannot be edited.
                self.assertNotIn((123, 900), bot.messages)
                self.assertNotIn("bot_emoji_customization", context.user_data)
                self.assertEqual(context.user_data["state"], "wait_edit_label")
                future = await bot.send_message(chat_id=456, text=message.text)
                self.assertTrue(future.text.startswith("🔥 تقييم الملف:"))
            custom._OWNER_LAST_MESSAGE = None
        asyncio.run(run_flow())

    def test_nonowner_cannot_open_forwarding_editor(self):
        async def run_check():
            bot = SimpleNamespace(id=888, send_message=AsyncMock())
            data = {}
            with patch.dict(os.environ, {"SUPER_ADMIN_ID": "123"}):
                result = await handle_forwarded_emoji_message(
                    forwarded_message(owner=124), bot, 124, 124, "private", data,
                )
            self.assertTrue(result)
            self.assertEqual(data, {})
            bot.send_message.assert_awaited_once()
            self.assertIn("غير معرّف كمالك", bot.send_message.await_args.kwargs["text"])
        asyncio.run(run_check())

    def test_myid_reports_owner_matching_without_revealing_other_owner_id(self):
        async def run_check():
            for configured, uid, expected in [
                ("123", 123, "تم التعرّف عليك كمالك"),
                ("999999999", 124, "لا تتعرّف على حسابك كمالك"),
                ("", 123, "لم يتم ضبط SUPER_ADMIN_ID"),
            ]:
                with self.subTest(configured=bool(configured), uid=uid):
                    message = SimpleNamespace(reply_text=AsyncMock())
                    update = SimpleNamespace(
                        message=message, effective_user=SimpleNamespace(id=uid),
                    )
                    with patch.dict(os.environ, {"SUPER_ADMIN_ID": configured}):
                        await message_handlers.cmd_myid(update, SimpleNamespace())
                    text = message.reply_text.await_args.args[0]
                    self.assertIn(str(uid), text)
                    self.assertIn(expected, text)
                    self.assertNotIn("999999999", text)
                    self.assertIn("صيغة التوجيه الحديثة", text)
        asyncio.run(run_check())

    def test_other_bot_forward_does_not_select_the_last_message(self):
        async def run_check():
            bot = SimpleNamespace(id=888, send_message=AsyncMock())
            data = {}
            with patch.dict(os.environ, {"SUPER_ADMIN_ID": "123"}):
                result = await handle_forwarded_emoji_message(
                    forwarded_message(source_bot=999), bot, 123, 123, "private", data,
                )
            self.assertFalse(result)
            self.assertEqual(data, {})
            bot.send_message.assert_not_called()
        asyncio.run(run_check())

    def test_forwarded_caption_is_selected(self):
        async def run_check():
            bot = SimpleNamespace(id=888, send_message=AsyncMock())
            data = {}
            with patch.dict(os.environ, {"SUPER_ADMIN_ID": "123"}):
                result = await handle_forwarded_emoji_message(
                    forwarded_message(text=None, caption="📚 وصف الملزمة"),
                    bot, 123, 123, "private", data,
                )
            self.assertTrue(result)
            self.assertEqual(data["bot_emoji_customization"]["target"]["text"], "📚 وصف الملزمة")
            self.assertTrue(data["bot_emoji_customization"]["target"]["is_caption"])
        asyncio.run(run_check())

    def test_forward_recovers_the_original_button_emojis_for_selection(self):
        async def run_check():
            owner = 733
            bot = FakeBot()
            bot.id = 888
            original_text = "⭐ تقييم الملف: 0\n👥 عدد التقييمات: 0"
            markup = custom.InlineKeyboardMarkup([[
                custom.InlineKeyboardButton("💬 التعليقات", callback_data="cmts_item:45"),
            ]])
            custom._OWNER_LAST_MESSAGE = None
            custom._OWNER_RECENT_MESSAGES = []
            data = {}
            overrides = MemoryOverrides()
            with patch.dict(os.environ, {"SUPER_ADMIN_ID": str(owner)}), \
                    patch.object(custom, "_collection", return_value=overrides), \
                    patch.object(custom, "_OVERRIDE_CACHE", {"expires": 0.0, "values": {}}), \
                    patch.object(custom, "_load_overrides", return_value={}):
                original = await bot.send_message(
                    chat_id=owner, text=original_text, reply_markup=markup,
                )
                result = await handle_forwarded_emoji_message(
                    forwarded_message(owner=owner, text=original_text),
                    bot, owner, owner, "private", data,
                )

            self.assertTrue(result)
            target = data["bot_emoji_customization"]["target"]
            self.assertEqual(target["message_id"], original.message_id)
            self.assertTrue(target["editable"])
            self.assertEqual(target["reply_markup"], markup)
            self.assertTrue(target["original_markup_found"])
            occurrences = data["bot_emoji_customization"]["occurrences"]
            self.assertTrue(any(
                item.get("scope") == "button" and item["glyph"] == "💬"
                for item in occurrences
            ))
            picker = bot.messages[(owner, bot.next_id - 1)]
            self.assertIn("الإيموجيات الموجودة في أزرار الرسالة الأصلية", picker.text)
            self.assertTrue(any(
                "زر: 💬 التعليقات" in button.text
                for row in picker.reply_markup.inline_keyboard for button in row
            ))

            button_index = next(
                index for index, item in enumerate(occurrences)
                if item.get("scope") == "button"
            )
            select_data = (
                f"emoji_custom_select:{data['bot_emoji_customization']['nonce']}:{button_index}"
            )
            context = SimpleNamespace(bot=bot, user_data=data)
            await custom.handle_owner_emoji_callback(SimpleNamespace(
                callback_query=FakeQuery(bot, select_data, owner, picker),
            ), context)
            await custom.handle_owner_emoji_input(
                SimpleNamespace(text="🔥", entities=[]), bot, owner, owner, "private", data,
            )
            preview = bot.messages[(owner, bot.next_id - 1)]
            self.assertEqual(
                preview.reply_markup.inline_keyboard[0][0].text, "🔥 التعليقات",
            )
            await custom.handle_owner_emoji_callback(SimpleNamespace(
                callback_query=FakeQuery(
                    bot, preview.reply_markup.inline_keyboard[-1][0].callback_data,
                    owner, preview,
                ),
            ), context)
            changed_button = original.reply_markup.inline_keyboard[0][0]
            self.assertEqual(changed_button.text, "🔥 التعليقات")
            self.assertEqual(changed_button.callback_data, "cmts_item:45")
            custom._OWNER_LAST_MESSAGE = None
            custom._OWNER_RECENT_MESSAGES = []
        asyncio.run(run_check())

    def test_forward_without_a_recent_original_explains_telegram_drops_buttons(self):
        async def run_check():
            owner = 734
            bot = FakeBot()
            bot.id = 888
            custom._OWNER_RECENT_MESSAGES = []
            data = {}
            with patch.dict(os.environ, {"SUPER_ADMIN_ID": str(owner)}):
                result = await handle_forwarded_emoji_message(
                    forwarded_message(owner=owner),
                    bot, owner, owner, "private", data,
                )
            self.assertTrue(result)
            target = data["bot_emoji_customization"]["target"]
            self.assertFalse(target["editable"])
            picker = bot.messages[(owner, bot.next_id - 1)]
            self.assertIn("Telegram لا يرفق الأزرار", picker.text)
            custom._OWNER_LAST_MESSAGE = None
            custom._OWNER_RECENT_MESSAGES = []
        asyncio.run(run_check())

    def test_real_bot_caption_send_hook_applies_saved_changes(self):
        async def run_check():
            caption = "📚 وصف الملزمة"
            source = SimpleNamespace(
                caption=caption, caption_entities=[], text=None, reply_markup=None,
                chat_id=456, message_id=7, chat=SimpleNamespace(id=456, type="private"),
            )
            updated = SimpleNamespace(**{**vars(source), "caption": "🔥 وصف الملزمة"})
            saved = {custom._template_key(caption): {
                "occurrences": {"0": {"0": {"glyph": "🔥", "custom_emoji_id": ""}}},
            }}
            bot = custom.EmojiCustomizationBot("123456:FAKE_TEST_ONLY_TOKEN")
            with patch.object(custom, "_load_overrides", return_value=saved), \
                    patch.object(ExtBot, "_send_message", new=AsyncMock(return_value=source)), \
                    patch.object(ExtBot, "edit_message_caption",
                                 new=AsyncMock(return_value=updated)) as edit:
                result = await bot._send_message("sendDocument", {})
            self.assertEqual(result.caption, "🔥 وصف الملزمة")
            self.assertEqual(edit.await_args.kwargs["caption"], "🔥 وصف الملزمة")
        asyncio.run(run_check())

    def test_text_command_accepts_diacritics_and_invisible_marks(self):
        self.assertTrue(custom.is_emoji_change_command("تَغيير\u200f الإيموجي"))


if __name__ == "__main__":
    unittest.main()
