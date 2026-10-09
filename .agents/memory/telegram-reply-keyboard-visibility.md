---
name: Telegram reply keyboard visibility
description: User preference for keeping or restoring the Telegram student control keyboard.
---

Keep the Telegram reply keyboard non-persistent; do not force it to stay open. `/start` restores the root keyboard. When a student sends unrecognized text outside an active input flow, show a clear prompt and resend the keyboard for the current menu without moving them elsewhere. Keep the empty-material notice separate. Wait for approval before adding other recovery controls.

**Why:** The user asked to remove the always-visible keyboard and chose unrecognized text as a natural way for students to restore the controls.

**How to apply:** Do not set Telegram's persistent-keyboard option. Restore controls on demand after unmatched text while preserving the student's current menu; do not add other recovery UI without approval.
