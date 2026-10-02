---
name: Telegram token log safety
description: Prevent Telegram bot API credentials embedded in request URLs from appearing in routine logs.
---

Telegram Bot API URLs contain the bot token in the path. HTTPX logs full request URLs at INFO level, so normal request logging can expose the token in workflow output.

**Why:** A live workflow emitted Telegram API URLs containing the configured bot token while starting the bot.

**How to apply:** Set the `httpx` logger to WARNING or higher before any Telegram API request. If a token has appeared in workflow logs, rotate it through BotFather and update Replit Secrets before restarting the bot.