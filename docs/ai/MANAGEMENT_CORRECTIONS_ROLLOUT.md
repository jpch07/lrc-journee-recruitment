# Management corrections rollout — 2026-09-28

## Scope and gates

Approved by user: uniform-target numeric corrections, independent manual color,
audit/restore/undo, both existing main Virginia and Oregon services. No hostname,
region, provider, secret, plan or independent-backup changes.

Fresh reviewer examined the full change. Three findings were reproduced and
fixed; follow-up review found no remaining findings within that scope:
custom-scale missing grades; concurrent scoring-input snapshots; General
Assessment configuration signatures. PostgreSQL uses REPEATABLE READ for the
whole preview/apply operation, including fresh configuration and scope reads.
Later overlapping grading changes may logically follow a correction; conflicting
correction saves return 409, never retry silently.

Local verification: 355 non-browser tests; both browser workflows passed,
including offline/conflict input retention. JavaScript syntax and diff checks
passed. Desktop/mobile editor and rank layout checked. Impeccable new-editor
detector returned no findings; existing theme is intentionally retained.

## Backup and rehearsal

Protected local backup directory: `%LOCALAPPDATA%/EvaldayBackups`.
`production-before-corrections-20260928.zip`: schema 0018, 35 tables, 7,788 rows.
Data SHA256: `dddabd9710caf51d6bd6859bd9c9d67d27a05923f3fabaaa064119bd47fc05ec`.
Archive contains confidential data; never commit or upload it to GitHub.

Restored and verified locally in `corrections-rehearsal-20260928.db`.
Migration 0018→0019 creates only the empty management_corrections table/index.
All original table hashes stayed identical. Existing calculated results, ranks
and every management/Journee workbook cell matched before/after across both
workspaces. Only response generatedAt timestamps are excluded from comparison.

Live readiness check: schema 0018; zero open activities in non-completed
Journees; three connected database clients at check time. Main and Oregon
health were ready. No production correction writes have been used as tests.

## Deployment sequence

Rank-only 6a27b15 live on both sites before schema change:
Oregon `dep-dat2hd8jo6nc73e0c2ug`; main `dep-dat2jujncjis73csevjg`.
The first Oregon attempt timed out before app startup; one unchanged retry
succeeded. Old release stayed healthy during the failed attempt.

Pending final release: push reviewed exact SHA, require CI, recheck no active
event, migrate once over TLS verify-full with one connection, deploy main then
Oregon sequentially. The old exact-revision readiness checks will temporarily
reject schema 0019 until each site receives this release: do not promise zero
interruption. Do not edit grades during this short transition. Services retain
their existing connection limits and auto-deploy remains disabled.

## Rollback

Keep schema/table and correction history. Before the first correction, the old
application's calculations are equivalent, but its exact-revision/startup guard
must be made compatible with additive schema 0019 before rollback. After any
correction exists, old scoring code is NOT a score-equivalent rollback. Prefer
a forward fix or a correction-aware previous release; export the correction
layer before any recovery operation. Never downgrade/drop this table or restore
an old full database over subsequent legitimate work.

## Final rollout evidence

Completed release: `ae84923578a4194143438fc5257b73a9a687443e`.
GitHub Actions `36400973117`: non-browser tests, both browser workflows and
Docker build all successful. Final independent review cleared all three fixes.

Final pre-cutover archive `production-final-before-corrections-20260928.zip`
matched the rehearsed backup exactly (same 35 tables, 7,788 rows and data hash).
Production migration ran once via TLS verify-full, one connection, an advisory
transaction lock and bounded statement/lock timeouts. Existing table hashes
before/after within that transaction were identical; new correction table empty.

- Main: `dep-dat2sqbbc2fs73avdrig`, live before 09:08:37 UTC.
- Oregon: `dep-dat2t5e0tbcc739lpugg`, live at 09:09:15 UTC.
- Both liveness/readiness endpoints returned 200 (`ok` / `ready`).
- viewer.js, admin.js, management-corrections.js and styles.css from both sites
  matched the exact release Git blobs (not Windows CRLF working-tree bytes).
- No error-level logs in the checked post-deployment intervals.
- Authenticated Oregon results: all 51 rendered rows exactly matched the saved
  pre-deployment rows. Profile editor loaded; a live read-only Sport preview
  correctly mapped each contributing criterion to 4/5, then was cancelled.
- Confirmed database schema 0019, zero saved management corrections and zero
  correction audit events afterward: no real grades were changed as a test.
- Main browser requested login; authenticated UI verification was performed on
  Oregon. Main deployment, health and exact asset checks passed independently.

The current main and Oregon share the production database and this release.
The independent backup and legacy service were not deployed or reconfigured.
Legacy schema-0018 code is not a compatible rollback once corrections are used.
