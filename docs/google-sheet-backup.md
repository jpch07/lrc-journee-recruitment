# Connect the manual Google Sheets backup

The backup writes only to:

https://docs.google.com/spreadsheets/d/11YSIJSpXWZZg00HlldQg3NLwKWQ-tGfrmQPPQF8Gbk0/edit

It runs only when the workspace owner presses **Back up workspace** in the Journee library. Keep that browser tab open until completion. There is no schedule, extra database, Drive folder or external photo file.

Keep the destination spreadsheet's **General access** set to **Restricted** and grant access only to intended backup administrators. The receiver web app may be available to **Anyone** because every request is signed, but the spreadsheet itself contains sensitive backup data and must not be public or published to the web. Hidden tabs are not an access-control boundary.

## One-time Google setup

Use the Google account that owns the spreadsheet. Do not attach a script to the spreadsheet itself: people with edit access could read an attached script. This receiver must be a separate **private standalone** project.

1. Open https://script.google.com/ and choose **New project**. Name it `Evalday workspace backup`.
2. Replace the contents of `Code.gs` with `integrations/google-sheet-backup/Code.js` from this repository.
3. Open **Project Settings** and enable **Show appsscript.json manifest file in editor**. Replace its contents with `integrations/google-sheet-backup/appsscript.json` from this repository. The manifest enables the Google Sheets v4 service. If it is not listed under **Services**, add **Google Sheets API**, version **v4**, identifier **Sheets**.
4. Under **Project Settings → Script properties**, add:
   - `WORKSPACE_ID`: the stable ID of the LRC recruitment workspace. As its logged-in owner, the response at `/api/admin/sheet-backup` contains `workspaceId`. Use the correct website/workspace session; do not guess from a Journee ID.
   - `BACKUP_SECRET`: a new random secret of at least 32 characters. Generate one locally with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Do not paste it into chat, the spreadsheet or source code. Keep it in private script properties and server secrets only.
5. In the editor select **authorizeBackup**, then **Run**. Complete Google's consent screen as the spreadsheet owner. This authorizes the script to read and edit the fixed spreadsheet and to manage its own installable trigger. The function removes duplicate `profileSelectionChanged` triggers for this spreadsheet and installs exactly one replacement. Open **Triggers** in the Apps Script sidebar and confirm one edit trigger for `profileSelectionChanged`. If your organization blocks consent, stop and resolve that with the account administrator rather than changing sharing or bypassing protections.
6. Choose **Deploy → New deployment → Web app**. Set **Execute as: Me** and **Who has access: Anyone**. The public endpoint still requires the signed secret on every request; it does not offer public backup read/write access. Deploy and copy the URL ending in `/exec`.

For an existing receiver, replace `Code.gs` and `appsscript.json` in the same private project, run `authorizeBackup` once to approve the added trigger-management scope, confirm the single trigger, then update the existing web-app deployment to a new version. Keep the existing deployment URL, script properties, execute-as setting and access setting; do not create a second receiver or paste secrets again.

The script project remains private even though its web endpoint is reachable. Do not share the script project or put the secret into its source. Do not create a second receiver for the same spreadsheet: main and Oregon must use the same receiver so its lock coordinates them.

## Application setup

Deploy this application revision, then set these server environment variables on each website that should offer this same workspace backup:

| Variable | Value |
| --- | --- |
| `LRC_SHEET_BACKUP_WORKSPACE_ID` | The same workspace ID as `WORKSPACE_ID` |
| `LRC_SHEET_BACKUP_URL` | The deployed Apps Script `/exec` URL |
| `LRC_SHEET_BACKUP_SECRET` | The same private value as `BACKUP_SECRET` |

Do not replace any database or R2 variables. These settings do not change the application's database, photographs or results.

Sign into the website as the workspace owner. In the Journee library, press **Check connection**, then **Back up workspace**. Completion is valid only when the UI reports Google read-back verification passed. Confirm the first tabs are **Results**, **Recruit Profiles**, and **Backup - Backup summary**. On the first two tabs, only `B3` and `E3` should be editable. Change the Recruit Profiles Journee selector and confirm the recruit selector resets before the profile photo and details refresh. Open `Backup - Photos` and verify representative previews. The first successful real upload is an integration acceptance step; local mocked tests alone do not prove it.

## What the spreadsheet contains

The first two tabs are native interactive management views for completed Journees: **Results** provides scoped overall, dimension and activity rankings, while **Recruit Profiles** provides scoped profiles, two radar charts and selector-driven photos. Their helper data is hidden and protected, and selectors never write back to Evalday. All remaining named tabs retain the backup data for every retained Journee, people, accounts/access, activity attendance, mandatory placements, rooms, assignment versions, original and admin evaluations, corrections, assessments, configuration and audit history. **Field details** expands nested criteria/configuration and long comments into labelled rows. IDs remain available for matching records, but names lead the readable views.

**Photos** holds embedded previews. Hidden **_Photo bytes** holds the exact original photo bytes as ordered base64 chunks, with content type and SHA-256. Hidden **_Records** holds typed JSON record chunks, part counts and SHA-256. These are inside this spreadsheet, not links that depend on a live R2 bucket. Hidden tabs are not private.

Passwords, password hashes, managed passwords, active sessions, credential-bearing links and provider secrets are excluded. Restoring account identities and permissions therefore requires setting new passwords and access links. The summary lists exclusions and table counts. This is a workspace backup format, not a `pg_restore` archive or a one-click application restore feature.

Use `app.sheet_backup_export.reconstruct_records` to validate/reconstruct exported `_Records` rows (without the header). Its typed values follow `scripts.database_transfer.encode/decode`; original binary images come from `_Photo bytes`, not preview images. Reconstruct each photo by recruit ID, order by numeric Part, require Parts/count agreement, decode base64 and verify SHA-256. Never run a production restore without separately validating credentials, schema, foreign keys and result parity on a disposable database.

## Limits and failure recovery

- Both readable and technical data take space. The exporter currently stops above 64 MiB of serialized export data or 128 MiB of temporary upload operations. The receiver conservatively caps existing plus staged cell allocation at 10 million cells. These are safety limits, not promises of unlimited Google capacity.
- A complete previous backup and the new staged copy coexist until publication. The last complete version is replaced atomically only after the new records and photo bytes verify. Unrelated tabs are not overwritten. Protected staging tabs are editable only by the script owner; do not edit them during a run.
- Verification includes a second read of every uploaded cell batch, reconstruction and SHA-256 checking of every technical record, a source-anchored ordered record digest chain, and reconstruction/checksums of original photos. The original full-record manifest checksum remains available for restoration tools. Publication refuses missing verification steps.
- A failed step can be retried without appending duplicate records. If Google committed the final publication but its HTTP response was lost, retry discovers the publication marker.
- Temporary network/provider/quota failures get up to three attempts with one- and two-second backoff and fresh signed nonces. Persistent failures stop for an explicit Retry upload; they never silently publish incomplete data.
- Cancel stops the run; incomplete staging is removed on the next manually started run. Closing the page stops the browser-driven upload. After a server restart, start again after the remote lease's 15-minute idle expiry. No partial snapshot is shown as complete.
- The application stores temporary operation files only for the active job; completion/cancellation/expiry closes them. The browser sees progress, not export bytes or Google credentials. Expired local jobs are cleaned on the next backup/status request; OS process shutdown releases open temporary files.
- Google quotas, consent revocation, provider outages and a full spreadsheet can prevent completion. Errors are reported; no fallback writes to another file and no paid upgrade happens automatically.
- The snapshot covers data at its database snapshot time. Changes made afterward, including the backup's own completion audit event, appear in the next backup.

## Verification commands

```powershell
python -m pytest tests/test_sheet_backup_export.py tests/test_sheet_backup_transport.py tests/test_sheet_backup_api.py tests/test_sheet_backup_ui.py -q
node --test tests/sheet_backup_receiver.test.cjs
$env:RUN_PLAYWRIGHT='1'
python -m pytest tests/test_sheet_backup_ui.py -q
```

All tests use fictional/disposable data. Browser UI tests and mocked Google lifecycle tests are distinct from live Google consent and end-to-end verification.
