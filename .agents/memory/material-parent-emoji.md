---
name: Study-material teacher-button emoji inheritance
description: The special parent-icon rule for teacher buttons inside study-material subject menus.
---

Teacher folder buttons (`compound`) inside the «ملازم» sections inherit the subject button's custom emoji. This inheritance is specific to study materials and does not apply to unrelated menus.

**Why:** The user clarified that teacher buttons should take their icon from the subject parent, and reported a new primary-school Arabic teacher button missing that icon.

**How to apply:** Prefer an explicitly selected emoji, otherwise copy the material subject's custom emoji when creating the teacher button; also resolve the parent style while building the keyboard so older teacher buttons without a saved style render correctly.
