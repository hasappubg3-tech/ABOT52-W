---
name: Granular Telegram admin roles
description: Product intent and compatibility decisions for restricted bot administrators.
---

A supervisor with only AI material-upload permission should otherwise behave like an ordinary member. Button-management permission must not imply access to bot settings.

**Why:** The user explicitly requested these two independent role configurations, rather than treating every supervisor as a full administrator.

**How to apply:** Preserve ordinary navigation and student features for restricted supervisors. Enforce capabilities at execution time, including pending inputs and old inline messages; hiding controls alone is insufficient.

Keep existing supervisors' rights until someone explicitly configures their role. Once configured, absent capabilities are denied.

**Why:** Introducing permissions must not unexpectedly disable the existing administrators, while new capabilities must not silently broaden explicitly restricted roles.

**How to apply:** Preserve this compatibility distinction when extending the permission catalog. Keep the principal administrator protected against removal or restriction and prevent delegated administrators from granting rights they do not have.

Backup restoration is reserved for the principal administrator; backup download can be delegated.

**Why:** Restoring data is more powerful than ordinary administration and can overwrite configuration and access data.

**How to apply:** Do not combine restoration with the delegated download capability.
