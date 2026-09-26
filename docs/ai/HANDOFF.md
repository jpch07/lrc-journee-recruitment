# Handoff to the next coding session / Codex
Status: PLANNED ONLY; adapted for Gemini CLI in VS Code. This template does not indicate any implementation or test has occurred.

## Current checkpoint
- Working folder: intended separate lrc-management-gemini folder; verify locally
- Branch: intended management-gemini-fixes; verify locally
- Baseline commit:
- Last reviewed commit:
- Selected task: onboarding only, then local test setup, then A
- Completed tasks: Task A, C1 performance optimization
- Next smallest step: C2 — Batch profile evaluation/history loadin

## Accepted product rules
- Numeric propagation rule (E0 is proposed until accepted):
- Overlapping adjustments and restore/undo:
- Overall ranking population / Journee eligibility:
- Duplicate attendance / rubric-version policy:
- Who may edit scores and colors:
- General factor and aggregate editing behavior:

## Latest change
- What changed and why:
- Relevant files/symbols:
- Remaining uncommitted changes:
- Known limitations:

## Tests — record evidence, not assumptions
| Command/check | Environment | Result | Evidence/notes |
|---|---|---|---|
| Not yet run | None | NOT RUN | Planning only |

Keep full logs outside the brief. Summarize relevant failures with exact test names and errors.

## Database and release safety
- Test database (nonsecret identity only):
- Migration revision and isolated PostgreSQL test:
- Backup/restore verification:
- Code rollback:
- Data-safe behavior when rolling back (never drop audit/corrections):
- Deployment status: NOT DEPLOYED

## Continuity sources
- BRIEF.md: compact verified starting points, not the full history.
- TASKS.md: requirements, proposed defaults and acceptance checks.
- Historical decisions/source excerpts consulted:
- Access limitations or unresolved conflicts:

Do not paste secrets, personal recruit data, complete chat transcripts or huge logs here.
## C1 checkpoint — Results-only aggregation for completed profiles

Status: Implemented locally; focused regression tests passed.
Deployment: NOT DEPLOYED.

Application change:
- Added `_completed_results(db)` in `app/routes_viewer.py`.
- Completed-scope profile loading uses this results-only helper
  instead of building the complete attendance/management dataset.
- The full `/api/view/completed` endpoint remains unchanged.
- Existing scoring/ranking functions and completed-Journee
  eligibility remain unchanged.
- No database migration, cross-request cache, or frontend change.

Files:
- app/routes_viewer.py
- tests/test_viewer_performance_c1.py

Focused verification:
- Command: python -m pytest tests/test_viewer_performance_c1.py -s -v
- Environment: Windows, Python 3.11.3, pytest 9.1.1.
- Actual result: 9 passed in 6.94s.

Measured query counts:
- Fixture: two completed Journees with linked evaluator submissions.
- Original `_completed_view`: 39 SELECT queries.
- New `_completed_results`: 15 SELECT queries.
- Difference: 24 fewer SELECT queries.
- These measurements cover helper calls on local SQLite, not
  full HTTP requests or production response time.

Broader regression verification:
- Command: python -m pytest -m "not browser"
- Actual result: ============================================================== 286 passed, 1 deselected in 87.25s (0:01:27) ==============================================================

Remaining work:
- Production performance has not been measured.
- Per-evaluation/profile-history queries and redundant General
  assessment saves remain separate follow-up items.
- C2 has not been implemented.