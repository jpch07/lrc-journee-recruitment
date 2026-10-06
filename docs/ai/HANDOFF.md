# Handoff to the next coding session / Codex

## 2026-10-04 — Manual single-spreadsheet workspace backup

- Status: IMPLEMENTED AND VERIFIED LOCALLY in `lrc-management-release`, branch `codex/management-ranks-release`. NOT PUSHED, NOT DEPLOYED, GOOGLE NOT CONNECTED. This entry supersedes earlier backup planning only; older deployment entries below concern other features.
- Destination is exclusively spreadsheet `11YSIJSpXWZZg00HlldQg3NLwKWQ-tGfrmQPPQF8Gbk0`, replacing the earlier link. Owner-only library button starts a manual workspace snapshot; no autosave schedule, new database, Drive folder, external photo file, region or scoring change.
- Export explicitly inventories/scopes all application tables, retains typed exact non-secret records and relationships, readable tabs/expanded field details, existing computed grades/ranks, original evaluations/history, corrections, attendance, assignments, configuration and audit. Photos have embedded PNG previews plus original binary base64 chunks/checksums inside the same sheet. Authentication credentials/sessions/access tokens are excluded; identities and permissions remain.
- Implementation: `app/sheet_backup_export.py`, `sheet_backup_schema.json`, `sheet_backup_transport.py`, `sheet_backup_jobs.py`, `routes_sheet_backup.py`; router registration in `app/main.py`; `app/static/sheet-backup.js` and admin/CSS integration; private standalone Apps Script in `integrations/google-sheet-backup/`. No database migration.
- Signed timestamped requests, replay protection, shared receiver lease, per-job locking, literal RAW writes, bounded transient backoff, resumable operation sequence, capacity checks, protected staging and atomic replacement of only owned tabs. Previously completed backup stays intact on failed verification/publication. All cells are reread, each technical record reconstructed/hashed and bound to the source manifest via ordered digest chain, and original photo bytes reconstructed/hashed before publication.
- Local verification: full non-browser suite **384 passed, 3 deselected**; all browser workflows **3 passed** (including desktop/mobile backup UI); receiver **10 Node tests passed**. After adding an additional retained-evaluation/history/correction fixture, the complete exporter module passed **10 tests**. Real Python export operations ran through the JavaScript Google simulator, including Unicode chunking, original photos, linked accounts and unrelated-workspace exclusion. Node syntax and git whitespace checks passed. No real Google data was used in these tests.
- Final independent review required by executing-plans completed. Five important findings fixed: audit redaction, credential-bearing relative/nested/encoded URLs, long computed-result cells, pre-publication record integrity, and retry capacity accounting. Historical receiver regressions reproduced unsafe unverified publication and double-counting before passing against the fixed receiver. Desktop/mobile screenshots inspected; Impeccable kept this a small extension of the existing library, not a redesign.
- Live blocker: computer browser inventory failed twice with `Unable to load browser request-header policy`; no authenticated Apps Script creation/authorization or production setup could be performed. No Google consent, quota behavior, or real spreadsheet upload has been verified. Do not describe this as an active/live backup.
- Next activation steps are in `docs/google-sheet-backup.md`: spreadsheet owner authorizes a PRIVATE STANDALONE script (not bound to the publicly editable sheet), deploys its signed receiver, and sets `LRC_SHEET_BACKUP_WORKSPACE_ID`, `LRC_SHEET_BACKUP_URL`, `LRC_SHEET_BACKUP_SECRET` on each intended website. Both sites must use the same receiver. Then deploy the application and run one real manual backup/read-back acceptance check. Do not ask the user to paste secrets into chat.
- Public edit sharing is unchanged; the UI warns that photos and personal data are readable by anyone with sheet access. Hidden tabs are not security. No external provider/production data/configuration was changed in this implementation.
- Constraints: bounded 64 MiB serialized export, 128 MiB operation spool, conservative 10-million-cell staging budget; no one-click application restore. Browser must remain open during manual upload. Server restart loses the temporary job; the 15-minute idle lease allows a fresh run. Original passwords/access links must be reset on restoration. Related rows are found through names, IDs and tab/filter navigation, not generated formula hyperlinks.
- Commits before the final hardening pass: `0251206` (export) and `afd5044` (receiver/API/UI). Full execution evidence/rulings: `docs/superpowers/plans/2026-10-04-google-sheet-backup-progress.md`. Verification screenshots are ignored under `.impeccable/review/`; no user directory was removed.

## 2026-09-28 — Readable workspace audit

- Approved bounded change: readable event history and standalone workspace audit from the library, including archived Journees. No infrastructure or scoring changes.
- Shared audit presentation resolves recruit/evaluator/Journee names in bounded batch queries, formats human-facing changes, rounds display numbers and expands criterion details. Raw stored evidence is unchanged. Existing profile/per-Journee APIs retain their original before/after fields.
- Future dict-shaped audit payloads receive additive `_auditContext` name snapshots; loaded entities are reused to avoid extra normal edit queries. No schema migration. Old records without a recoverable name clearly say the person is no longer available.
- `/api/admin/audit` is admin-only, explicitly workspace-scoped, supports person/action/Journee/UTC-date filters and stable timestamp+ID cursor pagination. Account security events remain in the existing owner-only security log, not the shared feed. No photo columns loaded by name resolution.
- Library has Workspace audit; standalone `/admin/audit` deep links, search, reset, older events, loading/error/empty states. Per-Journee Settings shows recent 50 and Search full history. Shared renderer is also used by admin/management profile history.
- Validation: full non-browser run had 362 passes and one stale asset-version assertion (updated for both JS bundles); targeted reruns recorded below. Both browser flows exercised; audit flow passed search, expandable criterion details, deep-link reload, return navigation and mobile width. Desktop/mobile screenshots inspected in one batch; Impeccable audit-module detector returned no findings. Release evidence follows after deployment.
- Scope limitations: already-deleted historical entities with no name snapshot cannot be reconstructed; no fabricated names. Permanent Journee deletion still has the pre-existing cascading audit policy; this task does not change deletion behavior. Workspace audit is admin-only, matching the existing audit permission.
- Rollback target: `01d3961c650569b0c2e80f76e153d3ddcd73f712`; additive audit metadata is compatible. No production records changed by verification.
- LIVE release: `b163ec13178d8d64afc20c80065ebdf30739f488`. GitHub CI `36446169873` passed: 363 non-browser tests, 2 browser workflows and Docker build. Oregon `dep-dat8p30473hc73f6tdhg` live at 15:50:17 UTC; main `dep-dat8pl0jo6nc73en4rjg` live at 15:51:34 UTC (2026-09-28). Both readiness/liveness endpoints returned 200, all four audit/admin/viewer/CSS assets matched release bytes after UTF-8/newline normalization, and unauthenticated audit requests returned 401. Separate post-deploy error-log queries completed with no entries. Authenticated UI verification used fictional local/CI data, not live production accounts.

## 2026-09-28 — Activity-context correction redesign (current work)

- User approved: results table shows only workspace-wide completed-Journee `Rank`; profile ranks unchanged. Corrections live inside activity View evaluations, not a profile-wide panel.
- One activity grade sets every criterion to the same normalized achievement. A complete evaluation accepts individual grades, or raw duration/count results for target-based activities. Existing evaluator submissions and legacy admin evaluations remain unchanged.
- Shared contextual UI for admin/management: author/time, effective management evaluation, expandable corrected criteria, editable single/criterion form, preview/apply, automatic restoration and scoped history. Dimension correction is inside its breakdown; color has an Edit color action.
- Existing correction model/CAS/audit are reused; NO schema migration. New request fields `criterionValues`/`rawValues` are mutually exclusive and require a complete activity. Raw management entries and generated equivalents are retained in audit metadata.
- Raw inverse: higher-is-better uses target * normalized grade; lower-is-better uses target / normalized grade. Full marks use exactly the configured target. Fractional integer results and lower-is-better zero grades have no exact finite raw equivalent: preserve the exact effective grade and display that limitation, never fabricate/round an observed performance. Config currently uses continuous targets, not discrete grade bands.
- Changed: correction service/domain/schema, shared correction UI, viewer/admin integration and asset versions, scoped CSS, focused API/domain and browser tests.
- Verification: 359 non-browser tests passed; both browser workflows passed (including management full criterion save/restore, single activity save/restore, color save/restore, admin save/author display, no JS page errors, and mobile fit). Node syntax checks and git diff whitespace checks passed. Impeccable detector flagged only incumbent shared-theme patterns; correction surfaces use scoped calmer controls. Desktop/mobile inspected in two bounded batches. Deployment outcome follows below after rollout.
- No infrastructure, hostname, pricing, database-location or backup changes in this task.
- LIVE: application commit `01d3961c650569b0c2e80f76e153d3ddcd73f712`; GitHub CI `36418726237` passed all tests and Docker build. Oregon deploy `dep-dat5dbnpn0mc73avge50` live 12:00:30 UTC; main deploy `dep-dat5e3ojo6nc73eaqki0` live 12:01:54 UTC (2026-09-28). Both `/health/live` and `/health/ready` returned 200; all four served JS/CSS assets match the release after UTF-8 decoding/newline normalization. Separate post-release error-log queries succeeded with no entries. Production authenticated UI could not be rechecked because browser request-header policy loading failed twice; full authenticated flows passed against fictional local and CI data. No production corrections were saved during release verification.
- Rollback: redeploy prior correction-aware `ae84923578a4194143438fc5257b73a9a687443e` if necessary; normalized values remain compatible and audit metadata is additive. Do not roll back to pre-corrections code/schema.

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

## Live release verification - 2026-09-28

Runtime commit: e0e40187a061f4e69904f5c33a48810dce3f89ed.
GitHub Actions run 36393217638: non-browser tests, both browser workflows,
and Docker build all successful.

- Oregon: dep-dat1mivpn0mc73ahou30, live at 07:46:57 UTC.
- Main Virginia: dep-dat1nhjncjis73cp6j00, live at 07:48:55 UTC.
- Both /health/ready endpoints returned 200 ready. Both served the exact
  UTF-8 viewer asset from this release and referenced asset version 20260928.1.
- Neither service had error-level logs in the checked post-deploy interval.
- Authenticated Oregon UI: all 51 displayed rows retained their earlier
  values, comments and ordering after accounting for the reordered columns
  and added local rank. This is rendered-value parity, not a raw DB checksum.
- Overall-scope profile showed 1/51 and local 1/18. A single-Journee profile
  showed overall 8/51 and local 5/18. No live grades were edited.
- Main authenticated-session check was not completed: its separate management
  browser session requested login. Main health/assets/deploy/log checks passed.
- No schema/data migration, secret change, service rename, deletion, plan
  change, or provider change performed. Backup service untouched.

Rollback commits (redeploy exact SHA; no schema rollback needed):
Oregon feed5bdff4b57c6dc865caa7f5c9f4cb335ad6bb;
Main 258638de459d7b3091797e6e27d85ecd4aaa067a.
Auto-deploy remains disabled; deployment branches/configuration unchanged.

NEW USER DECISION: a target 4/5 activity/dimension sets every contributing
criterion to the same effective 4/5 equivalent, never proportional scaling.
Original evaluator grades remain unchanged. This supersedes the unapproved
headroom proposal. Full design for approval is recorded in
GRADE_ADJUSTMENT_DESIGN_DRAFT.md; numerical and manual-color corrections are
not implemented or deployed. Written design/implementation-plan approval
remains necessary before the correction-layer schema change.

## Rank refinement and correction planning - 2026-09-28

The user reports sending the Render support email; do not send another.
They approved the remaining correction design and explicitly approved the
rank refinement: unboxed #1 with smaller muted 'of 51' below it. Removed
the '· completed Journees' profile suffix without changing rank semantics.
Scope stays main Virginia plus Oregon; hostname move still awaits support.

Rank UI implementation/test files: viewer.js, styles.css, admin/viewer HTML
asset versions 20260928.2, browser smoke, dual-rank and Playwright tests.
Verification: new static regression failed first, then 12 targeted tests
passed; full browser workflow passed (52.71s), including mobile/desktop
screenshots inspected, stacked number/total layout, unranked state and no
page overflow. Node syntax and git diff checks passed. Impeccable detector
reported only pre-existing shared CSS patterns; incumbent theme preserved.
No DB, score, rank formula, provider, secret or plan changes.

New correction implementation plan:
docs/superpowers/plans/2026-09-28-management-corrections.md.
Planning skill requires plan review/execution-method choice before the new
correction-layer code and migration; async approval requested. Recommended
native implementation with separate whole-branch reviewer. Do not confuse
the implemented rank refinement with completed grade/color corrections.

## Approved corrections implementation — 2026-09-28

User approved the written plan and native execution with final reviewer.
Implemented the separate revisioned management correction layer, additive 0019
schema, shared scoring overlays and raw/automatic/effective provenance, atomic
preview/apply/restore/undo APIs, audit history, shared admin/management editor,
manual configured color independent of numeric grades/ranks, and export evidence.
General Assessment retains existing autosave and history.

All 355 non-browser tests and both browser workflows passed. Independent review
findings fixed and cleared; see CORRECTIONS_PROGRESS.md and
MANAGEMENT_CORRECTIONS_ROLLOUT.md for exact evidence/rollback limitations.
Current rank-only release 6a27b15 is live on main and Oregon. Correction release
was then deployed successfully as ae84923578a4194143438fc5257b73a9a687443e
to main and Oregon after CI 36400973117 passed. Schema is now
0019_management_corrections. Fresh backup/restore and exact original-table,
score, rank and export parity passed. Both health/assets passed; authenticated
Oregon 51-row parity and preview/cancel passed. Main browser requires login;
no production grades were edited. See MANAGEMENT_CORRECTIONS_ROLLOUT.md for
deployment IDs, evidence and rollback limitations. Older scoring code is not
a safe score-equivalent rollback after corrections are used.

## Interactive Google Sheet management views — 2026-10-06

- Every verified manual backup now publishes **Results** first and **Recruit Profiles** second, both scoped to completed Journees. **Backup - Backup summary** is third; the remaining readable and technical backup tabs retain their relative order and restoration semantics.
- The Excel and Google presentations consume one primitive management payload captured inside the same database snapshot as `_results` and `_completed_results`. Duplicate recruit labels remain deterministic and lookups use `journeyId:recruitId`.
- Presentation rows are literal `RAW` data. The signed protocol adds allowlisted `results-v1` and `recruit-profiles-v1` descriptors plus `layout`/`verifyLayout` operations at version 1. The receiver alone constructs fixed formulas, validations, conditional formatting, radar charts, selector-only protection and the initial verified profile image.
- The private standalone Apps Script owns one installable `profileSelectionChanged` edit trigger. `authorizeBackup` now installs it idempotently and requires the `script.scriptapp` scope. It reacts only to the published owned **Recruit Profiles** sheet at `B3`/`E3`, resets stale recruit selections, and verifies bounded PNG chunks before replacing the managed image.
- Replay after a lost acknowledgement replaces, rather than appends, managed charts, rules, protection and the profile image. Publication still requires complete literal, record, photo and presentation-layout verification.
- Deployment acceptance must use the exact tested commit on both Virginia and Oregon. Oregon is the primary live surface: update the existing Apps Script deployment, reauthorize once, confirm exactly one edit trigger, run the Oregon manual backup through the read-back-success message, then verify selectors, charts, photos, protection and tab order in the live spreadsheet.
