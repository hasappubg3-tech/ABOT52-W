import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.loader import load_bot_symbols

load_bot_symbols()

from bot import keyboards, message_handlers


class UnrecognizedMessageKeyboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_unrecognized_student_message_reopens_current_menu(self):
        message = SimpleNamespace(
            text="كلام غير مفهوم",
            entities=[],
            caption_entities=[],
            document=None,
            photo=None,
            video=None,
            audio=None,
            voice=None,
            reply_to_message=None,
            chat_id=555,
            chat=SimpleNamespace(id=555),
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(
            message=message,
            effective_user=SimpleNamespace(
                id=12345,
                username=None,
                first_name="Student",
            ),
        )
        context = SimpleNamespace(user_data={"pid": 44}, bot=SimpleNamespace())

        with (
            patch.object(message_handlers, "track_message"),
            patch.object(message_handlers, "update_user_info"),
            patch.object(message_handlers, "is_real_admin", return_value=False),
            patch.object(message_handlers, "is_admin", return_value=False),
            patch.object(message_handlers, "check_rate_limit", return_value=True),
            patch.object(message_handlers, "has_permission", return_value=False),
            patch.object(message_handlers, "get_buttons_user", return_value=[]),
            patch.object(message_handlers, "build_kb", return_value="current-menu"),
            patch.object(keyboards, "is_mlz_filter_button_press", return_value=False),
        ):
            await message_handlers.on_message(update, context)

        message.reply_text.assert_awaited_once_with(
            "💬 ما فهمت رسالتك. استخدم أزرار القائمة للمتابعة:",
            reply_markup="current-menu",
        )
        self.assertEqual(context.user_data["pid"], 44)


if __name__ == "__main__":
    unittest.main()
