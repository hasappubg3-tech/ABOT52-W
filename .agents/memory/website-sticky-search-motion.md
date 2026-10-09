---
name: Sticky search motion
description: Why scroll-responsive search must preserve layout throughout visibility changes.
---

Keep document geometry stable when hiding or revealing the sticky homepage search. Avoid animating flow dimensions or switching between normal flow and overlays at animation boundaries.

**Why:** Repeated student-facing tests showed upward jumps and a two-stage reveal when height, padding, or positioning changed during or after animation, even though class-state unit tests passed. Scroll anchoring and the ongoing scroll motion make those layout changes visible.

**How to apply:** Use visibility transitions that do not resize the document. Do not block direction changes until an animation completes. Verify intermediate reveal frames, rapid direction reversals, return to the page top, and that hidden sticky space does not intercept taps. State-only tests cannot establish visual smoothness.
