---
name: Telegram file verification
description: Distinguish Telegram download-size limits from a missing or undeliverable material.
---

Do not classify a stored material as broken merely because Telegram getFile rejects it with "file is too big."

**Why:** A material already uploaded to the storage channel returned that download-size-limit error. That error concerns fetching the file through getFile, not sending an already stored document or copying its channel message.

**How to apply:** Distinguish a size-limit error from an invalid file identifier before repairing or removing file records. Verify the button-resolution and delivery paths separately; do not send unsolicited test documents to users.
