# Manual workspace backup to one Google spreadsheet

## Agreed outcome

The workspace owner presses **Back up workspace to Google Sheets** in the Journee library. The application exports the entire selected workspace, including every retained Journee, to the existing spreadsheet:

https://docs.google.com/spreadsheets/d/11YSIJSpXWZZg00HlldQg3NLwKWQ-tGfrmQPPQF8Gbk0/edit

There is no schedule, Drive folder, separate photo file, or synchronization back into the application. The spreadsheet holds the latest complete backup. Editing its cells does not change website data. The initial destination is for the LRC recruitment 2026 workspace; scoring and export logic remain workspace-configurable.

## What is included

Readable, consistently formatted tabs contain:

- Workspace configuration, rubric versions, activities, criteria, dimensions and performance bands.
- Accounts, linked account identities, access profiles and workspace/Journee permissions.
- All retained Journees, recruits, evaluator directories and event attendance.
- Activity availability, mandatory placements, recruit/evaluator rooms, locks and retained room-plan versions.
- Assignments, stable task identities and retained publication versions.
- Original evaluations, submission history, admin evaluations and management corrections.
- General assessments, comments, notes, calculated/effective grades, colors and ranks.
- Workspace audit history with names, readable changes and original non-secret event details.
- Every referenced recruit photo, both as a visible preview and as exact original bytes.

Tabs use frozen headers, filters, readable names, explicit units and links between related records. Stable IDs remain available for matching and restoration but are not the primary human-facing labels. Existing scoring functions supply results; this feature introduces no scoring changes.

Technical tabs retain the original typed records, relationships, configuration and photo data necessary to reconstruct the exported workspace. JSON and binary data are split into numbered, conservatively sized text chunks. A manifest records schema/export format versions, snapshot time, record counts, exclusions and checksums. Hidden technical tabs are a readability convenience, not a security measure.

### Security boundary

As agreed, the export excludes passwords (including hashes and encrypted managed passwords), authentication/session tokens, credential-bearing access links, provider credentials and server secrets. Account identities and permissions are included, but restored accounts require new authentication credentials. Audit/configuration payloads are checked for credential-bearing fields too.

“Everything” means retained application workspace data, not other organizations' data, deleted records, unreferenced historical image objects or provider infrastructure logs. Exclusions are listed explicitly in the manifest. A schema change cannot silently omit a new table or field: the exporter must classify it or stop with an actionable error.

The destination currently permits anyone with its link to edit. Uploading will expose recruit photos, evaluations and personal information to those people. Sharing will not be changed silently; the backup UI warns about the sensitive contents. Credentials are never placed in the sheet.

## Small integration, not a new backup platform

Use a workspace-scoped export service, a small Google Apps Script receiver, and the library button/status UI. Reuse snapshot, serialization and checksum patterns from `scripts/database_transfer.py` and readable labels/photo conversion from `app/report_exports.py`.

The existing workspace-seed exporter is insufficient: it copies accounts/configuration, not the complete Journee history. The new exporter explicitly scopes every included table through workspace ownership and parent relationships. It must never export the entire multi-workspace database as a shortcut.

The receiver is a **private standalone** Apps Script project, not a script attached to the publicly editable spreadsheet. It accepts only authenticated, replay-protected requests from the application and writes only to the fixed destination. Its secret stays in server configuration and private script properties. The owner must authorize Google access once; public link-edit permission does not replace this authorization. No paid resources are introduced.

Apps Script can [insert actual image blobs into sheets](https://developers.google.com/apps-script/reference/spreadsheet/sheet#insertImage(BlobSource,Integer,Integer)), with a 2 MB image limit. Display previews are converted/resized appropriately; original bytes are separately stored as base64 chunks inside the same spreadsheet. A preview or a link to R2 is never treated as the photo backup.

## Button execution and failure handling

1. Require workspace-owner authorization and CSRF protection. Acquire a shared workspace/destination lock so main and Oregon cannot publish competing backups.
2. Read a consistent database snapshot. Materialize it before Google network calls so uploads do not hold a database transaction open.
3. Retrieve referenced photos and verify size/checksum. Missing or unreadable images fail the run rather than disappearing from it.
4. Preflight spreadsheet capacity, including temporary staging space. Write bounded batches into staging tabs in this same spreadsheet. Treat user text as literal data, never executable formulas.
5. Verify reconstructed technical records and photo bytes against the manifest. Publish the new tab set and completion marker only after verification; remove the previous backup-owned tabs afterward. Preserve unrelated existing tabs.
6. Show the successful snapshot time and spreadsheet link, or an actionable failure. Keep the previous complete backup on failure. An interrupted run never appears complete; retry replaces its incomplete staging data.

The UI shows Not connected, Ready, Backing up, Complete, or Failed/interrupted. There is no persistent background-worker service. Bounded authenticated requests advance the manually started run; closing the page can interrupt it safely. Google's [execution limits](https://developers.google.com/apps-script/guides/services/quotas) and [Sheets request quotas](https://developers.google.com/workspace/sheets/api/limits) require batching and backoff, not one enormous request. If the workspace cannot fit, report that honestly without truncation or creating other files.

## Verification and delivery

Use fictional local fixtures covering every included data category and at least two workspaces. Verify isolation, credential exclusions, exact photo reconstruction, retained submission/correction history, identical calculated results, literal text handling, repeat runs, concurrent clicks, quota errors and interrupted publication. Reconstruct the technical export into a disposable test representation and compare counts, relationships and checksums.

Live completion requires the one-time Google authorization plus a successful manual export and read-back verification of this spreadsheet. Local tests alone do not prove that connection. No production database, region, scoring, autosave, failover or scheduling changes belong to this task.

## Review status

Specification approved; on 4 October 2026 the user supplied the replacement spreadsheet above, accepted owner-account authorization, and explicitly requested implementation without further questions. Preserve that instruction during execution.
