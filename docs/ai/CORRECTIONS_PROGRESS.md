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
