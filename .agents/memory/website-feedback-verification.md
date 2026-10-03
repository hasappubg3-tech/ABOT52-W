---
name: Website feedback verification pitfalls
description: Nonempty real feedback and cross-site preview sessions must be covered when testing synchronization.
---

Test feedback through the actual search-index/attachment resolution path with existing nonempty bot feedback, not only handcrafted button fixtures or an arbitrary empty note.

**Why:** A selective MongoDB projection dropped the bot's feedback-mode setting. Unit fixtures supplied that setting, and checking an empty note against the bot produced equal zero counts, so both checks passed while real historical feedback was invisible.

**How to apply:** Cover projected metadata in regression tests and compare a real note known to have prior ratings/comments. Never claim two-way synchronization solely from matching empty results.

Replit's embedded preview is a cross-site iframe. Guest sessions need cookie settings that work there, including third-party-cookie restrictions; standalone screenshots and Flask clients do not reproduce that browser context.

**Why:** Ordinary SameSite=Lax cookies prevented guest identity/CSRF persistence in the preview, and redirecting errors through cookie-backed flash messages hid the submission failure.

**How to apply:** Preserve secure, cross-site-compatible partitioned cookies, and render validation failures directly so they remain visible even when a browser blocks cookies. Verify persistent session behavior through the proxied URL as well as unit tests.

Python HTTPS diagnostics in this environment may need the system CA bundle rather than Requests' bundled certificates.

**Why:** Requests could not verify the proxied development URL with its default trust store; the system bundle successfully verified it.

**How to apply:** For shell-based HTTPS tests, use `/etc/ssl/certs/ca-certificates.crt` as the verification bundle. Do not disable certificate verification to work around this.