# Temporary production request timing — 26 September 2026

## Purpose and isolation

User-approved diagnostics of the site-wide latency after moving to Aiven.
Baseline: deployed commit `1faeb9647624995aed3373e474a70f641bc1fa6f`.
Target service: `evalday`, `srv-da61170u01pc738qvcag`.
Do not deploy to `evalday-legacy`, change `main`, or merge this temporary branch.
The user's local C1/C2 branch is separate and is not included.

Two application files only: `app/main.py` and new `app/request_timing.py`.
No changes to scoring, permissions, SQL statements, migrations, database data,
connection limits, dependency versions, infrastructure, or credentials.
The usual existing startup code still runs on deployment.

## Capture window and privacy

Capture ends automatically at **2026-09-28 00:00 UTC**. After that, the middleware
passes requests through without collecting, logging, or adding timing headers.
It does not arrange or claim a later automatic rollback.

Only existing `/health/live`, `/health/ready`, `/api/admin/…`, and `/api/view/…`
requests are measured. No diagnostic requests or database queries are added.
No response buffering and no reading of request/response bodies.

Outputs contain numbers, fixed category/phase names, and a random 12-character
correlation ID. They never contain raw paths, workspace or recruit IDs, SQL text,
bind parameters, query strings, cookies, passwords, response data, or account names.

`Server-Timing` and `X-LRC-Timing-Id` are added to sampled-path responses.
Existing Server-Timing values and every other response header/body are preserved.
Numeric `LRC_TIMING_V1` application logs are bounded to 150 API + 20 health lines
per process. Headers remain available until expiry if a log budget is exhausted.
All timing state is request-local and shared across this request's worker threads,
not a global cache of database or authorization state.

## Interpretation — overlapping measurements, not additive stages

- `lrc_app`: app-entry to response headers. Excludes client internet latency,
  reverse-proxy scheduling before the app, and body transmission/background work.
- `lrc_sql`: sum of client-side cursor execution durations. Includes network
  exchanges and server waits during execute. NOT PostgreSQL execution time alone.
  Excludes connection checkout, driver pre-ping, transaction close, separate row
  fetch/ORM conversion, except where these occur within a measured execute.
- `lrc_queries`: number of measured cursor executions (includes SET LOCAL on
  PostgreSQL). It is not a count of every network round trip.
- `lrc_sql_max`: slowest measured cursor execution; `lrc_sql_errors`: failed executes.
- `lrc_workspace`, `lrc_runtime`, `lrc_cookie`: entire existing synchronous helper
  durations; these INCLUDE SQL time in that helper. Context propagation and the
  unchanged per-request database checks remain intact.
- Readiness only: `lrc_acquire` wraps the existing `Session.connection()` call.
  It includes connection acquisition/creation/checking and transaction setup,
  NOT pool-queue time alone. `lrc_ping` and `lrc_revision` wrap the two existing
  readiness queries. `lrc_ready` includes the complete readiness handler.
- Logs also group cursor milliseconds by phase (`handler` means the rest of
  the request). Do not subtract these and claim the residual is CPU time.

The patch cannot independently isolate PostgreSQL lock waits, network latency,
or pre-ping/connection-creation costs. The measurements narrow those hypotheses.

## Verification

Run the existing CI unchanged. New tests exercise no body logging, preservation
of payloads/headers/streaming/errors, query counting across BaseHTTPMiddleware
and worker boundaries, concurrent request isolation, bounded logs, automatic
expiry, listener idempotence, and full-app auth/health behavior on test SQLite.
No performance gain is claimed; this is instrumentation, not an optimization.
Unit tests on a different local environment do not replace project CI.

## Deployment and rollback

Select the exact tested diagnostic commit on **evalday only**. Do not use
"Deploy latest commit", which points at main and does not include these changes.
Render's dashboard "Deploy a specific commit" disables auto-deploy for that
service; record that change and leave it off during the short diagnosis.

Rollback target is `1faeb9647624995aed3373e474a70f641bc1fa6f` (previous live deploy
`dep-dapac5jbc2fs73evtus0`). Use an exact-commit deployment/rollback, not a reset
of the shared Git branch or a database downgrade. No new migration is added.
After rollback and verification, restore the previously recorded auto-deploy
setting (On Commit) only as an explicit release step. No legacy-service changes.


## Logging correction (not a deployment)

The first project-level diagnostic run reported:
`8 failed, 284 passed, 1 deselected in 184.49s (0:03:04)`.
All eight failures concerned missing diagnostic log records.

Confirmed reproduction: the existing migrations/env.py calls Python
logging.config.fileConfig(alembic.ini) with its default settings. That disables
the already-created uvicorn.error logger previously used by the diagnostic.
This can suppress logs after application startup, not just affect pytest capture.

The diagnostic now writes only its bounded numeric LRC_TIMING_V1 lines directly
to stderr, captured by Render, instead of depending on uvicorn.error. No global
logging setting, migration source, database data, permission, schema, timing
calculation, or response body is changed by this correction.

The tests capture the actual stderr output. Existing assertions remain; two
regression tests were added for real fileConfig startup and output-sink failure.
The original eight failures were reproduced in an isolated environment.
After correction: `16 passed in 1.57s` with the migration logging configuration
preloaded (Linux, Python 3.13.5, pytest 9.0.2; not the full project).
Full project verification on the user's PC is still REQUIRED and NOT YET RECORDED.
No commit, push, or deployment has been performed by the repair helper.

## Verified local project test result

The diagnostic logging repair was applied before this run.

Command: python -m pytest -m "not browser" -q

Actual result:
294 passed, 1 deselected in 187.03s (0:03:07)

The preparation/repair helper ran this test selection in the separate
diagnostics worktree with the disposable SQLite test database.
No production database URL was supplied.

This verifies the selected local tests. It does not establish production
performance, PostgreSQL timing, or completion of the excluded browser tests.

Deployment status at this checkpoint: NOT DEPLOYED.
The C1/C2 worktree and main branch remain separate.
