---
name: Telegram reply keyboard visibility
description: User preference for keeping or restoring the Telegram student control keyboard.
---

Keep the Telegram reply keyboard non-persistent; do not force it to stay open. The existing `/start` flow restores the keyboard. Suggest any additional recovery control, such as an inline “show keyboard” button, and wait for the user's approval before implementing it.

**Why:** The user asked to remove the always-visible keyboard while still wanting students to find it easily.

**How to apply:** Do not set Telegram's persistent-keyboard option. Prefer on-demand restoration and do not add new recovery UI without approval.
