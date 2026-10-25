import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from telegram import MessageEntity

from bot import emoji_customization as custom


class MemoryOverrides:
    def __init__(self):
        self.rows = {}

    def find(self, query, projection=None):
        return list(self.rows.values())

    def update_one(self, query, update, upsert=False):
        row = self.rows.setdefault(query["_id"], {"_id": query["_id"], "occurrences": {}})
        path, value = next(iter(update["$set"].items()))
        _, line, position = path.split(".")
        row["occurrences"].setdefault(line, {})[position] = value


class FakeBot:
    def __init__(self):
        self.next_id = 1
        self.messages = {}

    async def send_message(self, chat_id, text, entities=None, **kwargs):
        message = SimpleNamespace(
            chat_id=chat_id, chat=SimpleNamespace(id=chat_id, type="private"),
            message_id=self.next_id, text=text, entities=list(entities or ()),
        )
        self.messages[(chat_id, self.next_id)] = message
        self.next_id += 1
        return message

    async def edit_message_text(self, chat_id, message_id, text, entities=None, **kwargs):
        message = self.messages[(chat_id, message_id)]
        message.text = text
        message.entities = list(entities or ())
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
                custom.install_bot_emoji_customization(bot)
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

                preview = bot.messages[(123, 3)]
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
