# C2 - Batched profile evaluation and history loading

Status: VERIFIED LOCALLY. Focused C1/C2 and non-browser regression checks passed.
Deployment: NOT DEPLOYED. No production access or migration.
Previous user-verified checkpoint: 924facc (C1), with
`286 passed, 1 deselected in 87.25s (0:01:27)`.

## Scope

Only app/routes_admin.py changes application behavior, by replacing repeated
submission/history SELECTs with request-local batched SELECTs. The existing
per-activity assignment queries and their returned ordering are retained.

- `_submission_payload` accepts an optional keyword-only `versions` list.
  Omitted/None retains the existing standalone history query; [] does not query.
- `_profile_evaluation_details` reads the assignments the old code read, then
  bulk-loads their submissions and complete version histories.
- `recruit_profile` calls that helper. Admin overrides, audit history, scoring,
  rank computation, permissions, serialization, and C1 code remain unchanged.
- All version records remain, ordered newest version first per submission.
- Missing submissions and draft submissions remain represented as before.
- No cross-request cache, DB writes, schema change, or migration is introduced.

## Verification before the user-PC runs (historical)

Before applying: Python syntax was checked in ChatGPT's execution environment.
The extracted batching/serialization functions were exercised against a small,
independent SQLAlchemy/SQLite harness: 6 tests passed. On a synthetic fixture
of five activities with two submissions each, helper SELECTs fell from 25 to 7.
That is NOT a run of this repository's full tests or a production benchmark.
The whole repository could not be checked out into that execution environment.

Checks pending at installation, now completed (verified results below):
- `python -m pytest tests/test_viewer_performance_c1.py tests/test_profile_performance_c2.py -s -v`
- Broader regression suite after focused checks pass.

The supplied C2 regression module uses C1's committed local fixture helpers.
It compares entire profiles for management (both scopes) and admin, checks full
histories/drafts/missing/noncurrent assignments, unchanged serializer callers,
no-query behavior for prefetched empty history, and next-request freshness.
At installation, 9 versus 4 helper SELECTs was an expectation. It has
since been measured on the user PC; see the verified checkpoint below.
C1 tests are rerun for tenant isolation, login, scope, scoring and freshness.

## Files and rollback

Changed: app/routes_admin.py
Added: tests/test_profile_performance_c2.py
Added: docs/ai/C2_CHANGE_NOTES.md (this file)

The installer prints the location of a byte-for-byte backup in the operating
system temporary directory, outside the project. Do not use git reset/clean or
restore whole files later if they acquire additional work. If rollback is
needed before further edits, restore only the backed-up routes_admin.py and
remove only the two C2-generated files after checking for later changes.
No commit or push is performed by the installer. No secrets are read.

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
