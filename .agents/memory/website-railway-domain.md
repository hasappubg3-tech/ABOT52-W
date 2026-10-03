---
name: Railway public deployment
description: Hosting and domain context for the public study-materials website.
---

The user's public website uses the custom domain `alameer-iq.com` and is hosted on Railway, separately from the Replit workspace preview.

**Why:** The user described the custom domain and Railway hosting; checks confirmed production content can differ from the workspace preview.

**How to apply:** Do not imply Replit preview edits are live on the public site. Verify changes on Railway after its deployment updates, and use the public HTTPS domain for production SEO checks.

The user requires sitemap page URLs to use the fixed canonical origin `https://alameer-iq.com`, not the incoming request host or scheme.

**Why:** Railway's public sitemap previously advertised HTTP URLs despite the HTTPS site; the user explicitly requested the HTTPS primary domain for search-engine compatibility.

**How to apply:** Keep sitemap URLs and the robots sitemap advertisement consistent with this origin. Do not let a preview hostname or stale HTTP configuration override it.