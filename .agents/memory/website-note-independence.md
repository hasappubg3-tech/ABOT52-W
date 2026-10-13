---
name: Website note independence
description: Product rules distinguishing independent website notes from Telegram's grouped content buttons.
---

Every document must be a separate note on the website, even when several documents belong to one Telegram button. Opening one note must show only that file; other notes appear below as «ملفات مشابهة», never as «ملفات الملزمة».

**Why:** The user explicitly does not want the website to copy the bot's grouped-file presentation.

**How to apply:** Keep category, search, latest-content, and old note links consistent with independent file pages. Do not alter shared content data merely to separate the website's presentation.

The user's download rule: «كل الملازم قابلة للتحميل من تلكرام عن طريق البوت حصرا».

**Why:** The user rejected storage-channel message links and preview-availability checks as substitutes for downloading through the bot.

**How to apply:** Every note must offer its bot deep link regardless of website preview availability. Do not offer direct document downloads, PDF embeds, or links to storage-channel messages on the website. Keep related notes as separate recommendations.

Website-initiated Telegram downloads must deliver only the file selected on the website, even if its bot button contains multiple files.

**Why:** The user reported that clicking one website note sent all notes in its bot button and explicitly requested only the chosen note.

**How to apply:** Preserve the selected-file identity through subscription prompts and delayed delivery. Keep ordinary bot button navigation and legacy full-button links unchanged; never fall back to the whole button when a selected file is missing.

The user's instruction: «لا تعدل شيء بالبوت دون علمي».

**Why:** The user asked to keep the bot unchanged while correcting the website.

**How to apply:** A website change is not permission to edit the bot. Tell the user before making any bot changes.

Student/community Telegram group entries, including their nested pages, must remain bot-only and not appear on the study-materials website.

**Why:** The user specified the website is for study notes/books/summaries and identified the sixth-grade student group as bot-only.

**How to apply:** Filter group entries from website navigation, search, and direct pages, while retaining their database records and bot visibility.

Each material's main title must include its teacher, followed by the publication year and then the part/chapter when available; append its school grade/stage as the final suffix.

**Why:** The user asked for the teacher and grade to be clear in every material title, with the grade last, such as «ملزمة الكيمياء للأستاذ فلان 2026 الجزء الفلاني الصف السادس العلمي».

**How to apply:** Keep primary labels consistent across listing cards, search results, recommendations, and the file detail heading. Derive the grade from its menu ancestry, keep subtitles for category context, and avoid changing canonical attachment slugs when only the displayed title changes.