import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.loader import load_bot_symbols

load_bot_symbols()

from bot import keyboards, message_handlers


class WebsiteStatsCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_configured_owner_receives_the_private_web_app_button(self):
        reply = AsyncMock()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=12345),
            message=SimpleNamespace(reply_text=reply),
        )
        with patch.dict(os.environ, {"SUPER_ADMIN_ID": "12345"}):
            await message_handlers.cmd_webstats(update, SimpleNamespace())
        markup = reply.await_args.kwargs["reply_markup"]
        self.assertEqual(
            markup.inline_keyboard[0][0].web_app.url,
            keyboards.WEBSITE_STATS_URL,
        )

    async def test_non_owner_does_not_receive_a_dashboard_link(self):
        reply = AsyncMock()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=999),
            message=SimpleNamespace(reply_text=reply),
        )
        with patch.dict(os.environ, {"SUPER_ADMIN_ID": "12345"}):
            await message_handlers.cmd_webstats(update, SimpleNamespace())
        self.assertNotIn("reply_markup", reply.await_args.kwargs)


if __name__ == "__main__":
    unittest.main()
