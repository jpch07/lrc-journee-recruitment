# SDD ledger — plan: docs/superpowers/plans/2026-09-28-management-corrections.md

User approved native execution 2026-09-28. Base 6a27b15; isolated worktree verified.
Referenced using-git-worktrees, test-driven-development, verification-before-completion,
subagent-driven-development helper files are not installed. Fallback: direct test-first
commands and this persistent ledger; final fresh reviewer remains required.

Pre-flight: Tasks 1→2→3→4 share nested normalized criterion map and result metadata.
Task 3→4 share strict preview/apply contract. Task 5 deploys all. No interface conflict.
Ruling: use a tracked progress document because supporting ledger scripts are missing;
cost if wrong: extra documentation only, no runtime effect.
Ruling: add inputFingerprint to request from the outset (specified later in Task 3);
it detects evaluation/general changes between preview and save. Cost: another preview
if underlying scoring inputs changed.

Task 1: complete — domain RED (missing module), GREEN 10/10 including
additive/idempotent migration on disposable SQLite, configured scale/target,
signature and invalid-value cases. No production migration.
Task 2: in progress. Existing aggregate arithmetic will be retained for
unadjusted components, with overlays applied before overall/rank calculation.

Task 2: complete — 3 scoring tests RED then GREEN; 47 targeted scoring,
rank and C1/C2 regression tests passed; additional overlap/export test passed
(4/4 correction scoring tests). Existing original records remain unchanged.
Ruling: criterion provenance is profile-level data, not included in normal
list rows, to preserve list/profile row equality and avoid large list payloads.
Cost: clients use profile.criteria for detailed correction editing.
Ruling: existing configuration historical_impact already blocks scoring
structure changes while Journees exist. Keep this guard; additionally fail
closed with a visible conflict if a correction signature disagrees after
out-of-band configuration changes. Cost: results require correction review,
never silent reinterpretation of grades.
Task 3: in progress.

Task 3: complete — API tests RED at missing routes, GREEN 6/6: preview with
no writes, apply, stale revision, restore/undo, color-neutral scoring,
invalid bands, changed automatic inputs, CSRF, login/recruit scope and
injected audit failure rollback. CAS updates and unique initial insertion
protect concurrent correction saves. Added dedicated routes_corrections.py
to share admin/viewer contracts without duplicating route logic.
Task 4: in progress. Browser workflow test extended first; execution pending.

Task 4: implemented. Reused the existing Playwright workflow instead of a
separate browser module, preserving its isolated server/fixture. Browser
activity correction, restore, manual color/restore and desktop/mobile fit passed.
Impeccable detector for the new shared editor reported no findings.
Ruling: moved expected schema revision to Task 4 because local browser startup
correctly refused migrated schema 0019 while code still expected 0018.
Ruling: correction_scoring.py isolates provenance/overlay arithmetic from the
existing aggregate implementation. Normal list rows retain their old fields.

Task 5: in progress. Full non-browser suite initially passed 348 tests. One
earlier overlapping test run was interrupted and discarded; suites run serially.
Fresh protected export: 35 tables / 7,788 rows. Local restore verified its hash;
0018-to-0019 rehearsal preserved every original table hash. Old/new result,
rank and every export-cell comparison passed across both workspaces; only
request generatedAt is excluded. Production migration has NOT run.
Fresh independent reviewer found three issues; reproduced failures and fixed:
nonzero-minimum missing criteria normalize to zero, general factor scoring
configuration enters the signature, and before/after/apply now share one
repeatable-read snapshot with configuration reloaded inside it. SQLite upgrade
conflicts and PostgreSQL serialization conflicts return 409 without retry.
Added concurrent first/update saves, mid-calculation grading changes, tenant
isolation and immutable submission/version tests. Re-running full verification.

Task 4: complete. Final browser workflows 2/2 passed, including conflict and
offline entry retention; final bounded desktop/mobile confirmation inspected.
Task 5: complete. 355 non-browser tests passed; CI 36400973117 passed including
Docker build. Fresh final backup matched rehearsal. Production 0019 migration
preserved all35 original table hashes, then main and Oregon deployed reviewed
ae84923 sequentially. Health/assets checked on both; authenticated Oregon
51-row parity and no-write preview/cancel verified. Main browser requires login.
No saved corrections/audit events were created by live verification. Full
deployment IDs and rollback limits are in MANAGEMENT_CORRECTIONS_ROLLOUT.md.
