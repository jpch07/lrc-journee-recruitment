# Site-wide performance checkpoint
Scope: user instruction 2026-09-27 supersedes management-only briefs. No production writes/deployments/migrations/infrastructure changes.
Worktree: lrc-sitewide-codex; branch codex/sitewide-performance.
Base: origin/main bec5bc5 preserves newer backup isolation (#31), atop live baseline 1faeb96. Reviewed A/C1/C2 copied as 73e0466 / 423872c / cb2eae8 (source C2 0f1b7d0). Source worktrees untouched. Diagnostics 46bde68 NOT merged.
Plan: measure fictional linked fixture HTTP/SQL; consolidate middleware setup into one worker/session without caching permissions; bundle authorized activity workspace and reuse room-edit response; remove redundant Journee/dashboard request; implement acknowledged-state autosave; focused then integrated tests/browser/build; small commits; prepare release approval.
Safety: session checks/CSRF/tenant scopes stay authoritative; runtime published configuration read each request; no shared sessions; preserve operation GET initialization/commit and audited private room copy.
Access: Render get_service verified Free/Virginia/one instance/auto-deploy OFF. Docker daemon unavailable at initial check. Usage budget unavailable.
Next: baseline and implementation. Logs outside model context in OS temporary directory.

Checkpoint shared setup: one worker owns resolver/runtime/slug session; original resolution precedence untouched. Auth dependencies still recheck sessions/accounts per request. Focused 44 passed in 13.68s (Windows Python 3.11.3). Baseline pre-patch 45 SQL/6 checkouts/2 requests opening; activity 56/15/5. Post shared setup separate requests 44/4/2 and 56/10/5. New bundle 32/2/1 opening, 25/3/1 activity (fixture SQL includes writes on operation initialization; SQLite excludes PostgreSQL ping/SET LOCAL). Exact logs TEMP/lrc-focused-sitewide.log.
