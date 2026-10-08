---
name: Mini App keyboard focus
description: Distinguish opening search and focusing its input from showing the native phone keyboard.
---

Do not promise that programmatic focus on Mini App launch opens the native keyboard on every Telegram client.

**Why:** The user tested the search-launch button and reported that the website opened but the keyboard did not. Browser-preview screenshots cannot verify the native keyboard.

**How to apply:** Treat launch-time focus as best effort. Explain that tapping the visible input is the reliable user-driven path, and verify any stronger keyboard claim on a real Telegram mobile client.
