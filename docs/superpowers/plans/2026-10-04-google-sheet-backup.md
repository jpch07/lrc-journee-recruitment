# Google Sheet Workspace Backup Implementation Plan

> **For agentic workers:** Use executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking. User explicitly requested immediate inline implementation without further questions.

**Goal:** One owner-only library button exports all retained workspace data and actual photos to the user's replacement spreadsheet.

**Architecture:** Build a consistent, secret-redacted workspace snapshot, project readable tabs and exact technical chunks, then send authenticated bounded operations to a private Google Apps Script receiver. Stage and verify in the same spreadsheet, retaining the last complete publication until atomic promotion. A browser-driven job uses temporary server storage; no scheduler, new database or worker service.

**Tech Stack:** Existing FastAPI, SQLAlchemy, Pillow, httpx; Google Apps Script and Sheets advanced service; vanilla JavaScript.

**Spec:** ../specs/2026-10-03-google-sheet-workspace-backup-design.md

## Global constraints

- One spreadsheet; no external backup/photo folders or files in Drive.
- Owner authorization and CSRF; fixed configured workspace and destination.
- Exclude credentials, sessions and provider secrets; include all other retained workspace data, photos and historical versions.
- Never alter original submissions, scores, schema, provider region or unrelated sheets.
- Closing the browser or losing a process must not publish a partial backup.
- No paid resources or scheduled execution.

## Review focus

- Cross-workspace children and linked platform accounts must not leak unrelated identities.
- Credential keys nested in JSON and credential-bearing URLs must not reach cells.
- Formula-like names, large Unicode notes and images must round-trip exactly.
- A lost HTTP response and concurrent main/Oregon clicks must not duplicate or replace the wrong publication.
- Quota/capacity failures, missing photos and expired jobs must preserve the previous complete backup.

## Task 1: Snapshot and workbook projection

**Files:** Create app/sheet_backup_export.py, app/sheet_backup_schema.json, tests/test_sheet_backup_export.py.
**Interfaces:** build_export(engine, system_id) -> dict with manifest, tabs and photos; encode_operations(export) -> list of operation dictionaries; reconstruct_records(rows) -> restored typed export dictionaries for verification.

- [ ] Add failing tests for scope, explicit schema coverage, excluded secrets, full retained history and binary-photo checksums.
- [ ] Run `python -m pytest tests/test_sheet_backup_export.py -q` and observe missing-module failure.
- [ ] Implement explicit per-table scoping, schema inventory and redaction. Use repeatable-read snapshot and existing result_snapshot / completed-rank helpers. Read photos after releasing the DB transaction.
- [ ] Project readable tables with name lookups and flattened JSON detail rows; retain complete typed records as numbered technical chunks. Preserve long text through chunks rather than truncation.
- [ ] Verify round-trip in tests: `assert reconstruct_records(export['technical_rows']) == export['records']`; assert image SHA-256 and excluded workspace identity absence.
- [ ] Run tests green and commit the export unit.

## Task 2: Authenticated receiver and transport

**Files:** Create integrations/google-sheet-backup/Code.js, integrations/google-sheet-backup/appsscript.json, app/sheet_backup_transport.py, tests/sheet_backup_receiver.test.cjs, tests/test_sheet_backup_transport.py.
**Interfaces:** Receiver.call(action, payload) -> dict; actions begin, apply, status, abort. Apply takes runId, sequence and operation. Deterministic sheet IDs and sequence acknowledgements make retries safe.

- [ ] Add failing tests for HMAC validation, fixed destination, replay, lease conflict, sequence retry and read-back mismatch.
- [ ] Implement signed timestamped envelopes and strict Google endpoint validation. Never return/log secret-bearing upstream bodies.
- [ ] Implement script-wide lock/lease, bounded RAW cell writes, embedded PNG previews and read-back verification. Reject insufficient staging capacity before creating tabs.
- [ ] Promote only fully verified staged sheets using one Sheets batchUpdate. Ownership metadata identifies deletable sheets; refuse name collisions with unrelated tabs.
- [ ] Run `node --test tests/sheet_backup_receiver.test.cjs` and `python -m pytest tests/test_sheet_backup_transport.py -q`; commit green receiver/transport.

## Task 3: Manual-job API

**Files:** Create app/routes_sheet_backup.py, app/sheet_backup_jobs.py, tests/test_sheet_backup_api.py; modify app/main.py.
**Interfaces:** GET /api/admin/sheet-backup returns connection/status; POST /start returns jobId/progress; POST /{job_id}/advance processes one bounded operation; POST /{job_id}/cancel releases the run. All responses exclude export bytes and secrets.

- [ ] Add failing API tests for owner-only access, CSRF, workspace targeting, concurrent starts and process-loss errors.
- [ ] Implement ephemeral spooled operation files with opaque job IDs, expiry, cleanup and per-job locks. Check configured receiver before reading source records. Bind jobs to workspace and initiating account.
- [ ] Release request auth DB connections before network work. The remote lease serializes main/Oregon jobs; local admission prevents duplicate snapshot generation.
- [ ] Track complete/failed state without pretending process-local progress persists across restart. Audit explicit start/completion/failure without exporting those secrets.
- [ ] Run `python -m pytest tests/test_sheet_backup_api.py -q`; commit green API.

## Task 4: Library UI and setup instructions

**Files:** Create app/static/sheet-backup.js and docs/google-sheet-backup.md; modify app/static/admin.html, app/static/admin.js; add tests/test_sheet_backup_ui.py.
**Interfaces:** mountSheetBackup(host, {api, mutation}) attaches owner-only status and manual action, consumes the API above.

- [ ] Test disconnected, active, failure/retry and complete states, and no automatic start or scheduled polling.
- [ ] Add a compact library section using existing button/type styles, aria-live progress, spreadsheet link, plain privacy warning, disabled duplicate-click action and cancellation. Never insert server-provided strings as HTML.
- [ ] Explain exact owner-account standalone-script setup, Sheets advanced service, properties and server variables, without storing any credentials in the sheet or repository.
- [ ] Run browser checks at desktop/mobile with fictional data. Run syntax/UI tests and commit.

## Task 5: Final verification and handoff

- [ ] Run all new tests plus existing database-transfer, workspace-audit, ranking and scoring tests serially.
- [ ] Obtain one fresh whole-change review per executing-plans; fix important findings with regression tests.
- [ ] Document evidence and outstanding Google consent/deployment requirements in docs/ai/HANDOFF.md. Do not claim live connection or completed backup from mocked tests.
- [ ] No deployment or production upload is considered verified until Google authorization and end-to-end read-back succeed.
