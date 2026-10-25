---
name: Telegram forward compatibility
description: Handling newer Bot API forwarding metadata with the installed Telegram library.
---

Do not assume a newer Telegram Bot API field is exposed as a native attribute. Check the library's preserved unknown fields as well, and account for the same difference in incoming-message filters.

**Why:** With PTB 20.7, modern forward_origin payloads survive only in api_kwargs; both the native attribute and legacy forward_from/forward_date can be absent. Mocks exposing forward_origin directly made tests pass while the real bot treated forwards as unmatched text.

**How to apply:** Use real Telegram Message/Update deserialization in forwarding regression tests. Preserve owner-only checks and verify the original bot identity from metadata, rather than accepting matching visible text or a hidden sender name.
