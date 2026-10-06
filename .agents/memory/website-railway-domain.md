---
name: Railway public deployment
description: Hosting and deployment context for the public website and Telegram bot.
---

The user's public website uses the custom domain `alameer-iq.com` and is hosted on Railway, separately from the Replit workspace preview.

The Telegram bot also runs on Railway.

**Why:** The user confirmed both the public website and the bot are deployed on Railway; Replit preview changes do not update those production services.

**How to apply:** Do not imply Replit preview edits are live on Railway. Deploy website and bot code changes to their Railway services, and avoid running a second local long-polling bot against the same Telegram bot token.

The user requires sitemap page URLs to use the fixed canonical origin `https://alameer-iq.com`, not the incoming request host or scheme.

**Why:** Railway's public sitemap previously advertised HTTP URLs despite the HTTPS site; the user explicitly requested the HTTPS primary domain for search-engine compatibility.

**How to apply:** Keep sitemap URLs and the robots sitemap advertisement consistent with this origin. Do not let a preview hostname or stale HTTP configuration override it.