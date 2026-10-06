# Interactive Google Sheet Management Views Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish an interactive completed-Journee Results tab and Recruit Profiles tab at the front of every verified Google Sheet backup while leaving the remaining backup and restoration tabs unchanged.

**Architecture:** Capture one plain management-report payload inside the backup's existing database snapshot and share it with the Excel and Google renderers. Build two literal-data presentation grids plus versioned layout descriptors; the standalone Apps Script validates and idempotently applies only allowlisted formulas, formatting, charts, protections, final titles and profile-preview behavior before atomic publication. A single private installable edit trigger changes the selected profile and reconstructs its verified PNG preview from protected helper cells.

**Tech Stack:** Python 3.11, FastAPI/SQLAlchemy, openpyxl, Pillow, Google Apps Script V8, Google Sheets v4 advanced service, Node's built-in test runner, pytest, Playwright, Render.

**Spec:** `docs/superpowers/specs/2026-10-06-interactive-google-sheet-management-views-design.md`

## Global Constraints

- Combined and selectable presentation data contains completed Journees only.
- The first two final tab titles are exactly `Results` and `Recruit Profiles`; `Backup - Backup summary` is third.
- Every other existing readable and technical backup tab keeps its data, title, relative order, protection and restoration semantics.
- Literal source values always use Sheets `RAW` writes; no database/user string is executable as a formula.
- Only `B3` and `E3` on the two presentation tabs are editable; helper ranges, formulas and all other backup tabs remain protected.
- The selected profile photo changes with the dropdown and is reconstructed only from a bounded, verified PNG preview store in the same managed profile tab.
- Existing staging, sequence, digest, read-back verification and atomic publication guarantees remain mandatory.
- A presentation tab may use at most 128 columns; every non-presentation tab retains the current 60-column ceiling.
- No new runtime service, database migration, public photo URL or Drive file is introduced.
- Oregon is the primary live acceptance surface; Virginia receives the identical commit and readiness verification.

## Review Focus

- A source mutation during export must not let presentation ranks/profile fields differ from the technical records captured in the same snapshot; Task 1 adds the concurrent-mutation regression.
- Duplicate recruit names and a Journee selector change must yield deterministic unique labels and immediately reset a stale `E3`; Tasks 1 and 5 pin both behaviors.
- A lost Apps Script response followed by replay must leave exactly two charts, one owned protection, one profile image and one set of conditional rules; Task 4 forces this replay.
- A non-owned `Results` or `Recruit Profiles` sheet must stop the run without overwriting it, and no protection transition may make an entire presentation tab editable; Task 4 covers both cases.
- Formula-like user text and corrupt/oversized preview chunks must remain literal or fail before publication; Tasks 2, 3 and 5 exercise these inputs.

---

### Task 1: Extract one snapshot-consistent management report payload

**Files:**
- Create: `app/management_report_payload.py`
- Modify: `app/report_exports.py:250-618`
- Modify: `app/report_exports.py:759-1177`
- Modify: `app/sheet_backup_export.py:165-251`
- Create: `tests/test_management_report_data.py`
- Modify: `tests/test_workflow.py:81-115`
- Modify: `tests/test_dynamic_general_assessment.py:135-158`

**Interfaces:**
- Produces: frozen `ManagementReportSource` whose fields are primitive tuples/mappings copied while the database session is valid; it includes completed Journees, attendance, exact per-Journee result snapshots, assignments/submissions/admin evaluations, general assessments and profile audit events.
- Produces: `load_management_report_source(db: Session, *, include_criteria: bool = True) -> ManagementReportSource`.
- Produces: pure `build_management_report_payload(source: ManagementReportSource) -> dict[str, object]`; it performs no database or object-storage access.
- Produces: `build_management_report_workbook_from_payload(payload: dict[str, object], *, photo_png_by_profile_key: Mapping[str, bytes] | None = None) -> Workbook` plus the existing `build_management_report_workbook(db: Session) -> Workbook` compatibility wrapper.
- Produces: result payload keys `scopes`, `views`, `rows`; profile payload keys `scopes`, `optionsByScope`, `summaries`, `dimensions`, `activities`, `evaluators`, `criteria`, `audit`; attendance remains available for the existing Excel workbook.
- Consumes later: `app/google_sheet_presentations.py` receives only this primitive payload plus already-verified photo previews.

- [ ] **Step 1: Write the failing completed-scope and ordering tests**

Create `tests/test_management_report_data.py` with fixtures containing two completed Journees, one active Journee, tied ranks, one absent recruit, and duplicate recruit names. Pin the public contract:

```python
def test_payload_uses_completed_journees_and_deterministic_rank_order(db_session):
    source = load_management_report_source(db_session)
    payload = build_management_report_payload(source)
    assert payload["results"]["scopes"][0] == "All completed Journees"
    assert "Active day" not in payload["results"]["scopes"]
    overall = [row for row in payload["results"]["rows"]
               if row["scope"] == "All completed Journees" and row["view"] == "Overall ranking"]
    assert [(row["rank"], row["name"], row["journeyName"]) for row in overall] == sorted(
        [(row["rank"], row["name"], row["journeyName"]) for row in overall],
        key=lambda item: (item[0] in (None, ""), item[0] or 10**9, item[1].casefold(), item[2].casefold()),
    )
```

Add assertions that absent recruits remain in profile options, repeated names have unique deterministic labels, all-completed `overallRank/overallPopulation` and per-Journee `journeyRank/journeyPopulation` remain distinct, and every payload value is JSON-serializable.

- [ ] **Step 2: Run the focused test and confirm the missing module/API failure**

Run: `python -m pytest tests/test_management_report_data.py -q`

Expected: FAIL because `app.management_report_payload` does not exist.

- [ ] **Step 3: Move the completed management collector into the focused module**

Move the current `_collect`, `_combined_results`, `_management_data`, `_result_rows`, `_fallback_result` and profile-label logic without changing scoring. Load audit events and copy every needed ORM value into the frozen source before returning. Implement these stable entry points:

```python
def load_management_report_source(db: Session, *, include_criteria: bool = True) -> ManagementReportSource:
    journeys = list(db.scalars(
        select(Journey)
        .where(Journey.status == "completed")
        .order_by(Journey.event_date.desc(), func.lower(Journey.name))
    ))
    by_id = {journey.id: _collect(db, journey, include_criteria=include_criteria)
             for journey in journeys}
    snapshots = [(journey, by_id[journey.id]["results"]) for journey in journeys]
    return ManagementReportSource.from_loaded_rows(
        journeys=journeys, by_id=by_id, snapshots=snapshots,
        combined=_combined_results(snapshots), audit_events=_load_profile_audit(db, journeys),
    )


def build_management_report_payload(source: ManagementReportSource) -> dict[str, object]:
    return {
        "attendance": _attendance_payload(source),
        "results": _results_payload(source),
        "profiles": _profiles_payload(source),
    }
```

Make `_results_payload` sort every scope/view by `(unranked, rank, name.casefold(), journeyName.casefold(), profileKey)`. Preserve both all-completed and Journee rank/population fields before flattening. Keep a unique recruit name plain; format duplicates as `Name — Journee · N`, with `N` assigned after sorting by name casefold, event date, Journee name and stable IDs. Lookups always use `journeyId:recruitId`.

- [ ] **Step 4: Refactor the Excel renderer to consume the shared payload**

Change the Excel renderers to accept the primitive payload instead of querying/rebuilding rows. Implement `build_management_report_workbook_from_payload`; keep the workbook's selectors, XLOOKUP formulas, charts, embedded profile images, titles, styles and visible values unchanged. The compatibility wrapper loads one source/payload, constructs `photo_png_by_profile_key` from the current recruit photos, and delegates to the payload renderer.

- [ ] **Step 5: Capture the Google payload inside the existing read snapshot**

Inside `build_export`, while the `Session(bind=connection)` and active assessment definition are still open, load one source and pure payload, and derive `records['_results']` and `records['_completed_results']` from that source. Pass the Google copy of the primitive payload through the existing recursive `redact()` boundary before storing it for later photo assembly; the Excel consumer keeps its current human-authored text behavior. Do not open another database session after `read_snapshot` exits.

- [ ] **Step 6: Add the concurrent-mutation regression**

In `tests/test_management_report_data.py`, monkeypatch the post-snapshot photo lookup boundary to mutate a recruit/result source row in a second transaction. Assert the returned presentation name/score/rank still equals the technical `_results` record captured before the mutation.

- [ ] **Step 7: Run shared-payload and Excel parity tests**

Run: `python -m pytest tests/test_management_report_data.py tests/test_workflow.py tests/test_dynamic_general_assessment.py -q`

Expected: PASS, including the existing Excel selectors, formulas, two charts, image lookup and dynamic-factor assertions.

- [ ] **Step 8: Commit the shared payload**

```bash
git add app/management_report_payload.py app/report_exports.py app/sheet_backup_export.py tests/test_management_report_data.py tests/test_workflow.py tests/test_dynamic_general_assessment.py
git commit -m "refactor: share management report presentation data"
```

### Task 2: Build literal Google presentation grids and trusted layout descriptors

**Files:**
- Create: `app/google_sheet_presentations.py`
- Modify: `app/sheet_backup_export.py:278-339`
- Modify: `app/sheet_backup_export.py:360-449`
- Modify: `tests/test_sheet_backup_export.py`
- Create: `tests/test_google_sheet_presentations.py`

**Interfaces:**
- Consumes: the primitive dictionary from `build_management_report_payload` and verified `photos` entries containing `recruitId`, `journeyId`, `preview`, `name` and checksums.
- Produces: `build_presentation_tabs(payload: dict[str, object], photos: list[dict[str, str]]) -> list[dict[str, object]]`.
- Each produced tab is `{name, finalTitle, presentation, rows, layout}` where `presentation` is `results-v1` or `recruit-profiles-v1`, `finalTitle` is allowlisted, `rows` is a rectangular literal-string matrix, and `layout` contains only integers, booleans, enum names and cell/range coordinates.
- Produces: `layout_operation(tab: dict[str, object]) -> dict[str, object]` with `kind='layout'`, `version=1`, the logical tab name, presentation enum and bounded layout parameters.

- [ ] **Step 1: Write failing presentation-grid tests**

Create tests that assert the exact front-tab contract and formula isolation:

```python
def test_presentations_are_first_and_literal(sample_payload, sample_photos):
    tabs = build_presentation_tabs(sample_payload, sample_photos)
    assert [(tab["name"], tab["finalTitle"], tab["presentation"]) for tab in tabs] == [
        ("Results", "Results", "results-v1"),
        ("Recruit Profiles", "Recruit Profiles", "recruit-profiles-v1"),
    ]
    assert all(not cell.startswith("=") for tab in tabs for row in tab["rows"] for cell in row)
    assert tabs[0]["rows"][2][1] == "All completed Journees"
    assert tabs[0]["rows"][2][4] == "Overall ranking"
```

Also assert 128 columns or fewer, all rows rectangular, preview chunks decode to the original PNG and pass SHA-256, duplicate-name keys remain distinct, a source value `=IMPORTDATA(...)` remains literal in hidden data, and credential-bearing URLs/nested secret fields are absent from both presentation grids.

- [ ] **Step 2: Run the new tests and confirm the missing builder failure**

Run: `python -m pytest tests/test_google_sheet_presentations.py -q`

Expected: FAIL because `build_presentation_tabs` is undefined.

- [ ] **Step 3: Implement the Results literal grid**

Build rows containing the visible title/selector/header labels plus hidden helper blocks for result data, scope options and view options. Store no formula strings. Emit layout parameters including helper bounds, visible capacity, selector cells, header row, frozen row count, tab color and column widths. Default `B3`/`E3` values are literal selector values.

- [ ] **Step 4: Implement the Recruit Profiles literal grid and preview store**

Build visible labels/placeholders plus hidden summary, dimension, activity, evaluator, criterion, audit, dependent-option and preview blocks. Produce one PNG for every selectable profile: use the verified recruit preview when present and the existing generated `PHOTO NOT RECORDED` placeholder when absent. Chunk each PNG with the existing 12,000-character ceiling into rows:

```python
[profile_key, str(part), str(part_count), preview_sha256, chunk]
```

The profile layout descriptor carries only block start/end coordinates, selector cells, section rows, chart source ranges, image anchor and expected preview count. Assert expected preview count equals the number of selectable stable profile keys.

- [ ] **Step 5: Put the presentations before the unchanged backup tabs**

Split the current `readable_tabs` result so it no longer emits the basic readable Results tab. Assemble:

```python
export["tabs"] = [
    *build_presentation_tabs(presentation_payload, photos),
    *readable_tabs(records, readable_audit, photos, manifest),
]
```

Assert names are `Results`, `Recruit Profiles`, `Backup summary`, then the existing database/readable sequence ending in `Audit history`, `Photos`.

- [ ] **Step 6: Add layout operations after all literal verification**

Keep every `rows`, `image`, `verifyPhoto`, `verifyCells` and `verifyRecords` operation in the existing order. Then emit one unbatched `layout` and one `verifyLayout` per presentation tab before `publish`. Treat `prepare`, `layout`, `verifyLayout` and `publish` as batching barriers in `encode_operations`.

- [ ] **Step 7: Pin operation limits and hostile-input behavior**

Extend `tests/test_sheet_backup_export.py` to assert layout operations contain no source text/formula strings, request bodies remain under 1,300,000 bytes, batches remain at most eight children, presentation descriptors allow at most 128 columns, and ordinary descriptors still fail above 60.

- [ ] **Step 8: Run presentation/export tests**

Run: `python -m pytest tests/test_google_sheet_presentations.py tests/test_sheet_backup_export.py -q`

Expected: PASS.

- [ ] **Step 9: Commit the Google presentation builder**

```bash
git add app/google_sheet_presentations.py app/sheet_backup_export.py tests/test_google_sheet_presentations.py tests/test_sheet_backup_export.py
git commit -m "feat: export interactive Google Sheet presentation data"
```

### Task 3: Validate and apply presentation layouts in the Apps Script receiver

**Files:**
- Modify: `integrations/google-sheet-backup/Code.js`
- Modify: `tests/sheet_backup_receiver.test.cjs`

**Interfaces:**
- Consumes: `layout` and `verifyLayout` operations from Task 2.
- Produces: `finalTitle(descriptor) -> string`, returning only `Results`, `Recruit Profiles`, or `Backup - ${name}`.
- Produces: `applyPresentationLayout(op, state, ss) -> void` and `verifyPresentationLayout(op, state, ss) -> void`.
- Maintains: `state.tabs[].layoutVerified`, required for every presentation tab before publish.

- [ ] **Step 1: Extend the fake Sheets runtime before production code**

Add fake support for grid properties, formulas, data validation, conditional rules, charts, sheet tab color, merges, protected ranges, unprotected ranges and developer metadata. Expose object counts and effective selector editability to tests.

- [ ] **Step 2: Write failing validation/final-title tests**

Cover the allowlist and collision rules:

```javascript
assert.equal(receiver.finalTitle({name:'Results',presentation:'results-v1'}), 'Results');
assert.equal(receiver.finalTitle({name:'Recruit Profiles',presentation:'recruit-profiles-v1'}), 'Recruit Profiles');
assert.equal(receiver.finalTitle({name:'Photos'}), 'Backup - Photos');
assert.throws(() => receiver.finalTitle({name:'Anything',presentation:'results-v1'}), /TABS/);
```

Add complete-run cases where non-owned `Results` or `Recruit Profiles` already exists and assert `COLLISION` with no deletion or rename.

- [ ] **Step 3: Harden descriptor validation and prepare state**

Accept only the two presentation enums with `version === 1`, exact logical/final-title pairs, visible status, and at most 128 columns. Keep 60 columns for every other tab. Persist the presentation enum and layout verification flag in the bounded RUN state.

- [ ] **Step 4: Implement the trusted Results layout template**

For `results-v1`, construct all formulas in Apps Script from validated integer bounds. Apply title/selector/header styling, data validations, frozen rows, hidden gridlines/helper columns, widths, wrapped text, number formats, alternating fills and rank/status/color conditional rules. Formulas use fixed `FILTER`/`XLOOKUP` templates and never interpolate a cell value.

- [ ] **Step 5: Implement the trusted Recruit Profiles layout template**

Apply the profile selectors, dependent option range, summary formulas, dimension/activity formula tables, general/evaluator/criterion/audit sections, two radar charts and hidden helper columns. Reconstruct the initial PNG from the validated helper chunks and place exactly one image at the fixed profile anchor.

- [ ] **Step 6: Replace the staging protection safely**

Identify only the prepare-created protected range for the current owned staging sheet. Replace/update it with one owned sheet protection whose `unprotectedRanges` are exactly `B3` and `E3`; never delete non-owned protections and never leave the sheet without its owned protection between requests.

- [ ] **Step 7: Make layout replay replace owned objects instead of appending**

Before applying a template, remove only layout-owned charts, conditional rules and the fixed-anchor profile image, and replace/update the owned protection and formulas at their exact ranges. Force a lost-state replay in the test and assert exactly two charts on Recruit Profiles, zero on Results, one owned protection per presentation sheet, one profile image, and the expected conditional-rule counts.

- [ ] **Step 8: Verify presentation layout before publish**

`verifyLayout` must check selector values/validations, formulas at fixed cells, hidden helper ranges, frozen rows, chart count, profile-image count and owned protection/unprotected ranges. Publish must reject any presentation descriptor without `layoutVerified === true`.

- [ ] **Step 9: Preserve generic publishing for all other tabs**

Use `finalTitle` for collision checks and renames. Skip the generic row-one formatting/filter/180px widths only for presentation descriptors; run the existing generic branch unchanged for all other tabs. Preserve atomic deletion/rename plus completion metadata.

- [ ] **Step 10: Run receiver tests**

Run: `node --test tests/sheet_backup_receiver.test.cjs`

Expected: PASS, including original atomic/replay/tamper tests and new layout/final-title/protection tests.

- [ ] **Step 11: Commit receiver layout support**

```bash
git add integrations/google-sheet-backup/Code.js tests/sheet_backup_receiver.test.cjs
git commit -m "feat: render verified Google Sheet management views"
```

### Task 4: Add the scoped profile-selector trigger and one-time authorization

**Files:**
- Modify: `integrations/google-sheet-backup/Code.js`
- Modify: `integrations/google-sheet-backup/appsscript.json`
- Modify: `tests/sheet_backup_receiver.test.cjs`
- Modify: `docs/google-sheet-backup.md`

**Interfaces:**
- Produces: `installInteractiveProfileTrigger() -> void`, idempotently owning exactly one installable `onEdit` trigger for this project/spreadsheet.
- Produces: `profileSelectionChanged(event) -> void`, accepting edits only on final managed `Recruit Profiles!B3` or `E3`.
- Produces: `refreshProfilePhoto_(spreadsheet, profileSheet) -> void`, reconstructing one verified preview and replacing the fixed-anchor managed image.

- [ ] **Step 1: Write failing trigger-filter tests**

Test ignored edits from another spreadsheet, another sheet, multi-cell ranges, non-selector cells and staging/unowned sheets. Test accepted `B3` and `E3` edits only when sheet metadata matches the currently published run.

- [ ] **Step 2: Write failing selector-reset and photo tests**

On `B3`, assert `E3` becomes the first option in the chosen completed scope before photo refresh. On `E3`, assert the stable profile key selects the right duplicate-name record. Assert missing previews produce the fixed placeholder and corrupt count/hash/size throws without removing the last valid image.

- [ ] **Step 3: Implement idempotent trigger installation**

Use `ScriptApp.getProjectTriggers()` to remove only duplicate handlers named `profileSelectionChanged` for the fixed spreadsheet, then create exactly one replacement with `ScriptApp.newTrigger('profileSelectionChanged').forSpreadsheet(BACKUP_SHEET).onEdit().create()`.

- [ ] **Step 4: Implement scoped edit handling**

Require the event source ID, final sheet title, owned metadata/current published run, one-cell edit and exact selector A1 notation. For `B3`, recompute/flush the dependent option range and set `E3` to its first nonblank value. Then call `SpreadsheetApp.flush()` and refresh only the managed profile image.

- [ ] **Step 5: Extend authorization and manifest scopes**

Have `authorizeBackup()` verify Sheets access and call the idempotent installer. Add the minimal trigger-management OAuth scope `https://www.googleapis.com/auth/script.scriptapp` beside the existing spreadsheets scope; retain `executeAs: USER_DEPLOYING` and anonymous receiver access.

- [ ] **Step 6: Document the one-time upgrade**

Update `docs/google-sheet-backup.md` with the new manifest, running `authorizeBackup`, confirming exactly one edit trigger, deploying a new web-app version and verifying the two selector cells. Do not instruct users to paste secrets or re-create the receiver.

- [ ] **Step 7: Run trigger and receiver tests**

Run: `node --test tests/sheet_backup_receiver.test.cjs`

Expected: PASS.

- [ ] **Step 8: Commit trigger/setup support**

```bash
git add integrations/google-sheet-backup/Code.js integrations/google-sheet-backup/appsscript.json tests/sheet_backup_receiver.test.cjs docs/google-sheet-backup.md
git commit -m "feat: switch Google Sheet recruit profiles interactively"
```

### Task 5: Complete end-to-end regression coverage

**Files:**
- Modify: `tests/test_sheet_backup_export.py`
- Modify: `tests/test_sheet_backup_transport.py`
- Modify: `tests/test_sheet_backup_api.py`
- Modify: `tests/test_sheet_backup_ui.py`
- Modify: `tests/test_workflow.py`
- Modify: `docs/ai/HANDOFF.md`

**Interfaces:**
- Consumes: all Tasks 1-4 public interfaces.
- Produces: a real Python export operation stream that the Node fake receiver publishes and validates end to end.

- [ ] **Step 1: Extend the real-export simulator assertion**

Pipe `encode_operations(build_export(...))` into the Node fake as the current test does. Assert final titles/order, Results formulas/validations, Recruit Profiles charts/protection/image, Backup summary third, one Photos preview, and preservation of `My own notes`.

- [ ] **Step 2: Add repeat-run and interruption cases**

Publish two full presentation backups, inject a layout failure in the second, and assert the first Results/Profile tabs and completion marker remain intact. Retry the exact layout operation and assert publication succeeds without duplicate objects.

- [ ] **Step 3: Pin API/UI progress copy for layout steps**

Map `layout` and `verifyLayout` to safe user-facing messages in `app/sheet_backup_jobs.py`, such as `Building interactive management views…` and `Verifying interactive management views…`. Assert retry resumes the same step and final copy remains `Backup complete. Google read-back verification passed.`

- [ ] **Step 4: Run the focused cross-language suite**

Run:

```bash
python -m pytest tests/test_management_report_data.py tests/test_google_sheet_presentations.py tests/test_sheet_backup_export.py tests/test_sheet_backup_transport.py tests/test_sheet_backup_api.py tests/test_sheet_backup_ui.py tests/test_workflow.py tests/test_dynamic_general_assessment.py -q
node --test tests/sheet_backup_receiver.test.cjs
```

Expected: all tests pass.

- [ ] **Step 5: Run browser UI coverage**

Run: `$env:RUN_PLAYWRIGHT='1'; python -m pytest tests/test_sheet_backup_ui.py -q`

Expected: both desktop/mobile backup UI tests pass.

- [ ] **Step 6: Run the full regression suite**

Run: `python -m pytest -q`

Expected: all applicable tests pass; document any pre-existing skip separately rather than weakening assertions.

- [ ] **Step 7: Update the engineering handoff and commit**

Record the first-two-tab contract, trigger setup, operation/layout version and Oregon acceptance requirement in `docs/ai/HANDOFF.md`.

```bash
git add app/sheet_backup_jobs.py tests/test_sheet_backup_export.py tests/test_sheet_backup_transport.py tests/test_sheet_backup_api.py tests/test_sheet_backup_ui.py tests/test_workflow.py docs/ai/HANDOFF.md
git commit -m "test: verify interactive Google Sheet backup views"
```

### Task 6: Deploy and verify through Oregon

**Files:**
- Verify only: `integrations/google-sheet-backup/Code.js`
- Verify only: `integrations/google-sheet-backup/appsscript.json`
- Verify only: `docs/google-sheet-backup.md`

**Interfaces:**
- Consumes: the exact tested Git commit and existing Render/Apps Script configuration.
- Produces: the same live commit on Virginia and Oregon, one current Apps Script deployment, one installed edit trigger, and a completed verified production snapshot.

- [ ] **Step 1: Confirm the release candidate is reproducible**

Run `git status --short`, `git rev-parse HEAD`, the focused Python/Node suites, and `python -m pytest -q`. Require a clean worktree and record the exact SHA.

- [ ] **Step 2: Push and deploy the exact commit to both Render services**

Push the current branch, deploy the exact SHA to service `srv-da61170u01pc738qvcag` (Virginia) and `srv-dasn8re0tbcc738644sg` (Oregon), wait for both deployments to report `live`, then require HTTP 200 `{"status":"ready"}` from both `/health/ready` endpoints.

- [ ] **Step 3: Stage the private Apps Script upgrade**

Replace Code.gs and `appsscript.json` with the exact checked-in files. Before approving new OAuth scopes or updating the web-app deployment, show the pending Google action and obtain action-time user confirmation as required by the browser safety policy.

- [ ] **Step 4: Reauthorize and install exactly one trigger**

Run `authorizeBackup` as the spreadsheet owner, approve only the declared Sheets and trigger-management scopes, and inspect Apps Script Triggers to confirm one `profileSelectionChanged` edit trigger.

- [ ] **Step 5: Deploy the new Apps Script version**

Update the existing deployment rather than creating a second receiver. Preserve the deployment ID/URL and execute-as/access settings. Recheck the signed receiver status through the application.

- [ ] **Step 6: Run the real backup from Oregon**

Open `https://evalday-oregon.onrender.com/lrc-journee-recruitment-2026/admin`, start the manual backup, keep the owner page active, and retry only the same resumable job on transient Google acknowledgements until the UI reports `Backup complete. Google read-back verification passed.`

- [ ] **Step 7: Verify the Results tab interactively**

Confirm tab 1 is `Results`, defaults are All completed Journees/Overall ranking, ranks are ascending with deterministic ties/unranked rows last, and changing both dropdowns updates the table, status/color formatting and comments without changing website data.

- [ ] **Step 8: Verify Recruit Profiles interactively**

Confirm tab 2 is `Recruit Profiles`; change Journee and recruit selectors, including a repeated name if available. Verify `E3` resets on scope change, photo/identity/ranks/dimensions/activities/general assessment/evaluator/criterion/audit sections update, both charts remain exactly once, and missing-photo behavior is readable.

- [ ] **Step 9: Verify backup integrity and protections**

Confirm Backup summary is third with the new snapshot time/counts/checksum, the rest of the tabs match their prior names/order, selector cells are editable, formulas/helper cells and technical tabs are protected, and the receiver status is ready with the completed marker.

- [ ] **Step 10: Record deployment evidence**

Record both Render deployment IDs, exact SHA, Apps Script version, completion timestamp and visual acceptance results in the final handoff. Keep the verified spreadsheet open as the user-facing deliverable.
