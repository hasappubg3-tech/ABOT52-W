---
name: Telegram object immutability
description: Constraints when intercepting outgoing bot messages and modifying text entities.
---

The installed python-telegram-bot library freezes bot method attributes and message-entity fields. Use an ExtBot subclass for outgoing-message interception and reconstruct entities when changing offsets.

**Why:** Fake bot objects permit monkey-patching that the real library rejects. Likewise, shallow-copying a MessageEntity does not make its fields writable. Offline flow tests alone missed both differences.

**How to apply:** When adding message rendering hooks or entity transformations, test the real bot subclass with the Application builder without network access, and exercise real Telegram entity objects in unit tests.
