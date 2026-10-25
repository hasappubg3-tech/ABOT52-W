import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity

from bot import emoji_customization as custom


class MemoryOverrides:
    def __init__(self):
        self.rows = {}

    def find(self, query, projection=None):
        return list(self.rows.values())

    def update_one(self, query, update, upsert=False):
        row = self.rows.setdefault(query["_id"], {"_id": query["_id"]})
        path, value = next(iter(update["$set"].items()))
        section, line, position = path.split(".")
        row.setdefault(section, {}).setdefault(line, {})[position] = value


class FakeBot:
    def __init__(self):
        self.next_id = 1
        self.messages = {}

    async def send_message(self, chat_id, text, entities=None, **kwargs):
        message = SimpleNamespace(
            chat_id=chat_id, chat=SimpleNamespace(id=chat_id, type="private"),
            message_id=self.next_id, text=text, entities=list(entities or ()),
            reply_markup=kwargs.get("reply_markup"),
        )
        self.messages[(chat_id, self.next_id)] = message
        self.next_id += 1
        return await custom.customize_outgoing_message(message, self._raw_edit)

    async def edit_message_text(self, chat_id, message_id, text, entities=None, **kwargs):
        message = await self._raw_edit(chat_id, message_id, text, entities, **kwargs)
        return await custom.customize_outgoing_message(message, self._raw_edit)

    async def _raw_edit(self, chat_id, message_id, text, entities=None, **kwargs):
        message = self.messages[(chat_id, message_id)]
        message.text = text
        message.entities = list(entities or ())
        if "reply_markup" in kwargs:
            message.reply_markup = kwargs["reply_markup"]
        return message


class FakeQuery:
    def __init__(self, bot, data, uid, message):
        self.bot = bot
        self.data = data
        self.from_user = SimpleNamespace(id=uid)
        self.message = message
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    async def edit_message_text(self, text):
        return await self.bot.edit_message_text(
            chat_id=self.message.chat.id,
            message_id=self.message.message_id,
            text=text,
        )


class EmojiCustomizationTests(unittest.TestCase):
    def test_template_key_survives_counter_and_emoji_changes(self):
        self.assertEqual(
            custom._template_key("📚 عدد الملفات: 10"),
            custom._template_key("🔥 عدد الملفات: 500"),
        )
        self.assertEqual(
            custom._template_key("⭐ تقييم الملف: 0\n👥 عدد التقييمات: 0"),
            custom._template_key("🔥 تقييم الملف: 4.5\n👥 عدد التقييمات: 22"),
        )

    def test_custom_button_icon_preserves_action_and_future_item_ids(self):
        async def run_flow():
            bot = FakeBot()
            overrides = MemoryOverrides()
            markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("💬 التعليقات", callback_data="cmts_item:11"),
            ]])
            with patch.dict(os.environ, {"SUPER_ADMIN_ID": "123"}), \
                    patch.object(custom, "_collection", return_value=overrides), \
                    patch.object(custom, "_OVERRIDE_CACHE", {"expires": 0.0, "values": {}}):
                first = await bot.send_message(chat_id=123, text="التفاصيل", reply_markup=markup)
                user_data = {"state": "existing_owner_operation"}
                await custom.begin_emoji_customization(bot, 123, 123, "private", user_data)
                picker = bot.messages[(123, bot.next_id - 1)]
                data = picker.reply_markup.inline_keyboard[0][0].callback_data
                context = SimpleNamespace(bot=bot, user_data=user_data)
                await custom.handle_owner_emoji_callback(SimpleNamespace(
                    callback_query=FakeQuery(bot, data, 123, picker),
                ), context)
                await custom.handle_owner_emoji_input(
                    SimpleNamespace(text="🔥", entities=[
                        MessageEntity(MessageEntity.CUSTOM_EMOJI, 0, 2, custom_emoji_id="42"),
                    ]), bot, 123, 123, "private", user_data,
                )
                preview = bot.messages[(123, bot.next_id - 1)]
                save = preview.reply_markup.inline_keyboard[-1][0].callback_data
                await custom.handle_owner_emoji_callback(SimpleNamespace(
                    callback_query=FakeQuery(bot, save, 123, preview),
                ), context)
                button = first.reply_markup.inline_keyboard[0][0]
                self.assertEqual(button.callback_data, "cmts_item:11")
                self.assertEqual(button.to_dict()["icon_custom_emoji_id"], "42")
                self.assertEqual(button.text, "التعليقات")
                self.assertEqual(user_data["state"], "existing_owner_operation")
                future = await bot.send_message(
                    chat_id=456, text="التفاصيل",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("💬 التعليقات", callback_data="cmts_item:22"),
                    ]]),
                )
                future_button = future.reply_markup.inline_keyboard[0][0]
                self.assertEqual(future_button.callback_data, "cmts_item:22")
                self.assertEqual(future_button.to_dict()["icon_custom_emoji_id"], "42")
            custom._OWNER_LAST_MESSAGE = None
        asyncio.run(run_flow())

    def test_nonowner_cannot_use_customization_callback(self):
        async def run_check():
            bot = FakeBot()
            with patch.object(custom, "_load_overrides", return_value={}):
                message = await bot.send_message(chat_id=124, text="Test")
            query = FakeQuery(bot, "emoji_custom_save:nonce", 124, message)
            context = SimpleNamespace(bot=bot, user_data={})
            with patch.dict(os.environ, {"SUPER_ADMIN_ID": "123"}):
                result = await custom.handle_owner_emoji_callback(
                    SimpleNamespace(callback_query=query), context,
                )
            self.assertTrue(result)
            self.assertTrue(query.answers[-1][1])
        asyncio.run(run_check())

    def test_saved_emoji_is_applied_to_pyrogram_preview_messages(self):
        async def run_send():
            from bot import pyro_sender

            original = "📚 عدد الملفات: 12"
            key = custom._template_key(original)
            saved = {
                key: {
                    "occurrences": {
                        "0": {"0": {"glyph": "🔥", "custom_emoji_id": "saved-emoji-42"}}
                    },
                    "buttons": {},
                }
            }

            class FakePyrogramClient:
                def __init__(self):
                    self.sent = None

                async def send_message(self, **kwargs):
                    self.sent = kwargs
                    return SimpleNamespace(
                        chat=SimpleNamespace(id=123, type=SimpleNamespace(value="private")),
                        id=77, text=kwargs["text"], entities=kwargs["entities"],
                    )

            client = FakePyrogramClient()
            custom._OWNER_LAST_MESSAGE = None
            with patch.dict(os.environ, {"SUPER_ADMIN_ID": "123"}), \
                    patch.object(custom, "_load_overrides", return_value=saved), \
                    patch.object(pyro_sender, "_get_client", return_value=client):
                sent = await pyro_sender.send_animated(123, original, emoji_map={})
            self.assertTrue(sent)
            self.assertEqual(client.sent["text"], "🔥 عدد الملفات: 12")
            self.assertEqual(
                [entity.custom_emoji_id for entity in client.sent["entities"]],
                ["saved-emoji-42"],
            )
            self.assertEqual(custom._OWNER_LAST_MESSAGE["message_id"], 77)
            custom._OWNER_LAST_MESSAGE = None

        asyncio.run(run_send())

    def test_emoji_parser_keeps_zwj_flags_and_utf16_offsets(self):
        text = "👩‍👩‍👧‍👦 🇮🇶 ⭐"
        found = custom.emoji_occurrences(text)
        self.assertEqual([item["glyph"] for item in found], ["👩‍👩‍👧‍👦", "🇮🇶", "⭐"])
        self.assertEqual([item["offset"] for item in found], [0, 12, 17])

    def test_replacement_preserves_text_entity_positions_after_utf16_shift(self):
        text = "👩‍👩‍👧‍👦 bold"
        bold = MessageEntity(type=MessageEntity.BOLD, offset=12, length=4)
        result, entities = custom.replace_emoji(
            text, [bold], 0, "🔥", "custom-fire",
        )
        self.assertEqual(result, "🔥 bold")
        self.assertEqual(
            [(entity.type, entity.offset, entity.length, entity.custom_emoji_id)
             for entity in entities],
            [
                (MessageEntity.CUSTOM_EMOJI, 0, 2, "custom-fire"),
                (MessageEntity.BOLD, 3, 4, None),
            ],
        )

    def test_command_is_restricted_to_owner_private_chat(self):
        with patch.dict(os.environ, {"SUPER_ADMIN_ID": "123"}):
            self.assertTrue(custom.is_private_owner_message(123, 123, "private"))
            self.assertFalse(custom.is_private_owner_message(124, 124, "private"))
            self.assertFalse(custom.is_private_owner_message(123, -100, "group"))

    def test_owner_can_replace_one_emoji_and_reuse_override_for_future_messages(self):
        async def run_flow():
            custom._OWNER_LAST_MESSAGE = None
            bot = FakeBot()
            overrides = MemoryOverrides()
            user_data = {}
            with patch.dict(os.environ, {"SUPER_ADMIN_ID": "123"}), \
                    patch.object(custom, "_collection", return_value=overrides), \
                    patch.object(custom, "_OVERRIDE_CACHE", {"expires": 0.0, "values": {}}):
                first = await bot.send_message(chat_id=123, text="أولاً 🙂 ثم ⭐")
                await custom.begin_emoji_customization(
                    bot, 123, 123, "private", user_data,
                )
                picker = bot.messages[(123, 2)]
                select = picker.reply_markup.inline_keyboard[0][0].callback_data
                select_query = FakeQuery(bot, select, 123, picker)
                context = SimpleNamespace(bot=bot, user_data=user_data)
                await custom.handle_owner_emoji_callback(
                    SimpleNamespace(callback_query=select_query), context,
                )
                handled = await custom.handle_owner_emoji_input(
                    SimpleNamespace(text="🔥", entities=[]),
                    bot, 123, 123, "private", user_data,
                )
                self.assertTrue(handled)

                preview = bot.messages[(123, bot.next_id - 1)]
                save = preview.reply_markup.inline_keyboard[0][0].callback_data
                save_query = FakeQuery(bot, save, 123, preview)
                await custom.handle_owner_emoji_callback(
                    SimpleNamespace(callback_query=save_query), context,
                )
                self.assertEqual(first.text, "أولاً 🔥 ثم ⭐")
                self.assertNotIn(123, user_data)
                self.assertTrue(overrides.rows)

                future = await bot.send_message(chat_id=456, text="أولاً 🙂 ثم ⭐")
                self.assertEqual(future.text, "أولاً 🔥 ثم ⭐")
                self.assertEqual(len(custom.emoji_occurrences(future.text)), 2)
            custom._OWNER_LAST_MESSAGE = None

        asyncio.run(run_flow())


if __name__ == "__main__":
    unittest.main()
