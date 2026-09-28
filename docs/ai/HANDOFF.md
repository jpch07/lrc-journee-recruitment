# Handoff to the next coding session / Codex
Status: TASK A, C1 AND C2 IMPLEMENTED LOCALLY. Verified tests recorded below. NOT DEPLOYED.

## Current checkpoint
- Working folder: intended separate lrc-management-gemini folder; verify locally
- Branch: intended management-gemini-fixes; verify locally
- Baseline commit:
- Last reviewed commit:
- Selected task: C2 - Profile evaluation/history batching; implemented and tested locally
- Completed tasks: Task A, C1 performance optimization; C2 profile evaluation/history batching
- Next smallest step: C3 - Review redundant General-assessment autosaves (not implemented)

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

<!-- C2_VERIFIED_CHECKPOINT_START -->
## C2 verified checkpoint - Profile evaluation/history batching

Status: IMPLEMENTED LOCALLY; focused and broader regression checks passed.
Deployment: NOT DEPLOYED by this workflow.
Previous checkpoint: 924facc (C1).

Evidence source: user-provided terminal output reviewed in ChatGPT. This
recorder writes that evidence; it does not execute or independently rerun tests.
The following results describe the code inspected at this checkpoint only.

### Scope
- Application change: app/routes_admin.py only.
- Added request-local _profile_evaluation_details() to batch submissions and
  their complete version histories for the selected recruit's assignments.
- _submission_payload() accepts keyword-only prefetched versions. None retains
  its original standalone query; an empty list does not trigger another query.
- Existing per-activity assignment queries and their returned order remain.
- Histories remain newest-version-first within each submission.
- C1, scoring/ranking functions, authorization, admin adjustments, profile
  audit history, and frontend files are not changed by this patch.
- No cross-request cache, schema change, migration, or new write path.

### Verified local results
Environment: Windows, Python 3.11.3, pytest 9.1.1; disposable SQLite test DB.

Focused command:
`python -m pytest tests/test_viewer_performance_c1.py tests/test_profile_performance_c2.py -s -v`

Exact focused summary: `19 passed in 16.96s`.

Broader command: `python -m pytest -m "not browser"`

Exact broader summary: `296 passed, 1 deselected in 92.03s (0:01:32)`.
The deselected test was not run by that command. This is not a statement that
all possible tests, browsers, or production environments were verified.

C2 helper measurement on the two-linked-activity fixture:
- Original helper: 9 SELECT queries.
- Batched helper: 4 SELECT queries.
- Reduction: 5 SELECT queries.

C1 helper measurement remains 39 versus 15 SELECTs (24 fewer) for its separate
two-completed-Journee fixture. These are separate helper measurements, not a
combined full-request benchmark or a measured production speedup.

The focused runs included whole-profile comparison for management (individual
and completed scopes) and admin; complete submission histories; missing and
draft cases; no-query handling for empty prefetched histories; and visibility
of new history on the next request. C1 regressions also passed.

### Checkpoint files
- app/routes_admin.py
- tests/test_profile_performance_c2.py
- docs/ai/C2_CHANGE_NOTES.md
- docs/ai/HANDOFF.md

The patching and documentation helper programs are local tools, not part of
this checkpoint. Stage only the four files listed above.

### Remaining work and rollback
- Production response time and PostgreSQL behavior have not been measured.
- The existing per-activity assignment queries remain intentionally.
- Next planned task: C3, review redundant General-assessment autosaves and
  design a bounded fix that preserves edits, conflict handling, and retries.
- C3 and later ranking/grade/color features are NOT implemented here.
- No rollback has been performed. After committing, prefer a reviewed revert
  of the C2 commit over restoring entire files containing subsequent work.
- No database rollback or data deletion is needed for this read-only change.
<!-- C2_VERIFIED_CHECKPOINT_END -->

## Management ranks and color placement - 2026-09-28

Accepted product decision: Overall Rank includes only completed Journees in
the current recruitment/workspace. Journee Rank remains local. Preserve the
configured tie policy and existing present/active recruit eligibility.

Implemented in this worktree:
- Management overall tables and profiles now show both ranks and populations.
- Draft/active Journees retain a local rank but have no overall rank.
- Color is the first column in management and admin overall-results tables.
- No score formula, submission, schema, database, or history changes.
- Explicit workspace filtering protects the completed-rank lookup; selected
  table snapshots are reused rather than calculated a second time.

Changed application files: routes_viewer.py, routes_admin.py, services.py,
static/viewer.js, static/admin.js, static/styles.css.
Tests: test_management_dual_ranks.py (new), test_viewer_performance_c1.py.
Focused check (local disposable SQLite): 30 passed in 12.64 seconds across
dual ranks, C1, C2, and browser-asset smoke tests.

Release integration must start from the tested Oregon runtime commit feed5bd,
not deploy this older branch wholesale. Preserve all newer startup/performance
and autosave fixes. Both requested deployment targets are evalday (Virginia)
and evalday-oregon; the independent backup is not part of this release.

Still pending: numeric grade-correction design/approval, implementation and
audit/restore semantics; manual color override. No propagation algorithm has
been approved. Do not flatten or redistribute criterion grades by assumption.
The earlier numeric-change proposal is design-only, not authorization to
change live scores. Aiven migration and snapshot-backup work remain paused.

Release integration checkpoint:
- Cherry-picked onto feed5bd in codex/management-ranks-release; no conflicts.
- Asset versions bumped to 20260928.1 for admin/viewer and their stylesheet.
- Full non-browser suite: 327 passed, 2 deselected (90.25s).
- Browser workflow: passed including desktop/mobile rank-summary bounds,
  distinct labels and color-first table. Sitewide browser flow also passed.
- An initial parallel invocation collided on the shared disposable SQLite
  fixture; reruns were serial. A new browser assertion initially expected
  title case despite the existing uppercase CSS; assertion corrected.
- Both responsive screenshots inspected. No new overflow; retained incumbent
  design. Mechanical design scan reported only pre-existing CSS patterns;
  no unrelated redesign performed.
- JavaScript syntax checks and git diff --check passed.
- No migrations, scoring formulas, model definitions, or DB configuration
  changes in this release relative to the tested Oregon runtime.
- Deployment status will be recorded after live verification. Support email
  draft is in docs/RENDER_HOSTNAME_SUPPORT_EMAIL.md; it has NOT been sent.
