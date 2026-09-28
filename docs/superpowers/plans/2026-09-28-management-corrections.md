# Management corrections implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Let management correct effective criterion, activity, dimension and color grades without changing evaluator submissions, with reliable audit, restore, previews and consistent rankings/exports.

**Architecture:** Add one revisioned correction record per workspace/Journee/recruit. Numeric edits resolve to normalized criterion overrides; shared scoring consumes that map, while raw submissions and the existing automatic baseline remain separately available. Color is an independent configured-band override in the same record.

**Tech stack:** Existing FastAPI, SQLAlchemy, Alembic, PostgreSQL, Decimal, vanilla JavaScript/CSS, pytest and Playwright. No new dependencies.

**Spec:** `docs/ai/GRADE_ADJUSTMENT_DESIGN_DRAFT.md`, approved for implementation by the user's 2026-09-28 request. Uniform-target example explicitly approved: 2/5 and 4/5 become effective 4/5 and 4/5 when the activity is set to 4/5.

**Execution status:** completed natively with an independent final reviewer.
The checklist below is the original plan; actual test evidence, justified file/
sequence substitutions and rollout outcomes are recorded in
`docs/ai/CORRECTIONS_PROGRESS.md` and `docs/ai/MANAGEMENT_CORRECTIONS_ROLLOUT.md`.
Release ae84923 is live on main and Oregon, schema 0019. No unrelated
infrastructure or independent-backup changes were made.

## Global constraints

- Keep original submissions, version histories, measured Sport counts/times, and existing AdminEvaluation records unchanged.
- Overall ranking includes only completed Journees within the same workspace.
- General Assessment keeps its current autosave workflow and existing history.
- No provider, region, hostname, plan, or other infrastructure changes.
- No new background backup/autosave proposal.
- Work in `lrc-management-release`, branch `codex/management-ranks-release`, preserving the tested Oregon runtime ancestry. Do not deploy the older Gemini worktree.
- Both requested deployment targets are evalday (Virginia) and evalday-oregon; the independent backup is not part of this release.
- Do not delete correction data in a rollback. Older code ignores corrections and is not a score-equivalent rollback after use.

## Review focus

1. An incomplete dimension with all criteria manually graded must contribute under the configured exclude-missing policy without pretending evaluator submissions exist (Task 2).
2. An adjustment made on a stale screen must not silently overwrite a newer numeric or color adjustment, including simultaneous first saves (Task 3).
3. Renaming a criterion must not invalidate it, but changing its scale, target, mapping, weights or enabled state must expose an explicit configuration conflict (Tasks 1–3).
4. An activity target followed by an overlapping dimension target must recompute both; the first activity target is not a permanent aggregate override (Task 2).
5. A removed/renamed color-band key must never become arbitrary HTML/CSS or silently change numerical scores (Tasks 2–4).

## File ownership and interfaces

New focused modules:

- `app/management_corrections.py`: pure target resolution, normalization, configuration signatures and correction validation.
- `app/correction_service.py`: scoped reads, preview, optimistic save/restore/undo, audit transactions.
- `app/static/management-corrections.js`: shared grade/color editor markup and interaction binding, using injected API/reload callbacks rather than importing viewer globals.

Existing integration points:

- `app/models.py`, new `migrations/versions/0019_management_corrections.py`, `app/main.py`: persistence and expected schema revision.
- `app/services.py::result_snapshot`: one batch correction query per snapshot; preserve original path when no numeric overrides apply.
- `app/routes_admin.py::recruit_profile`, `_dimension_breakdowns`; `app/routes_viewer.py::profile_view`: raw/automatic/effective details and correction routes.
- `app/schemas.py`: strict typed operations; `app/utils.py::audit`: existing audit writer reused.
- `app/report_exports.py`: shared effective results and explicit automatic/effective correction provenance.
- `app/static/viewer.js`, `admin.js`, `styles.css`, HTML asset versions: editor entry points and consistent effective/manual labels.

Public Python contracts (no route-to-route imports in the new modules):

```python
resolve_targets(definition, level: str, key: str,
                activity_key: str | None = None) -> list[tuple[str, str]]
normalize_target(value: Decimal, minimum: Decimal, maximum: Decimal) -> Decimal
build_override_map(definition, current: dict, level: str, key: str,
                   value: Decimal, activity_key: str | None = None) -> dict
correction_signature(definition) -> str
read_corrections(db, journey, recruit) -> dict
preview_correction(db, journey, recruit, operation) -> dict
apply_correction(db, journey, recruit, operation, actor_name: str,
                 actor_type: str) -> dict
```

`current`/returned maps are nested by activity key then criterion key. Decimal normalized values are serialized as strings in storage, never binary floats. `operation` is the strict `ManagementCorrectionRequest` model described in Task 3. Snapshot overlays and previews share the same calculation, not a second approximate formula.

## Task 1: Correction domain and additive persistence

**Files:** Create the two Python modules above (service initially reads only), migration 0019 and `tests/test_management_correction_domain.py`; modify `app/models.py`.

**Consumes:** Active `AssessmentSystemDefinition`; current Journey/Recruit identifiers.
**Produces:** `ManagementCorrection` model and the five pure domain functions above; `read_corrections` with revision 0 and empty map for no record.

- [ ] Write domain tests first, including this exact approved rule:

```python
def test_uniform_target_uses_every_contributing_criterion():
    definition = active_assessment_definition()
    activity = next(a for a in definition.activities if a.enabled)
    changed = build_override_map(definition, {}, 'activity', activity.key, Decimal('4'))
    assert set(changed[activity.key]) == {c.key for c in activity.criteria if c.weight > 0}
    assert set(changed[activity.key].values()) == {'0.8'}
```

- [ ] Run `python -m pytest tests/test_management_correction_domain.py -q`; expect missing module/function failure before implementation.
- [ ] Implement `normalize_target` with finite/range checks and Decimal arithmetic:

```python
if not value.is_finite() or not minimum <= value <= maximum or maximum <= minimum:
    raise ValueError('Grade is outside its configured scale.')
return (value - minimum) / (maximum - minimum)
```

- [ ] Implement target resolution: criterion requires activity key; activity selects its positive-weight criteria; activity-sourced dimension expands its activity; criterion-sourced dimension selects matching criteria across enabled activities. Unknown/empty targets reject. Aggregate activity input is /5; dimension input uses displayMaximum; target-based criterion input is converted /5, never raw time/count. Input step does not round management averages.
- [ ] Add `ManagementCorrection` columns: id, system_id, journey_id, recruit_id, criterion_values_json, color_key nullable, configuration_signature, configuration_json, revision, updated_at, updated_by; unique `(system_id, journey_id, recruit_id)` and index on `(system_id, journey_id)`. All foreign keys scoped/validated on service reads and writes. Use text JSON and existing model conventions. Migration adds only this empty table/index; no historical backfill.
- [ ] Sign the scoring-relevant normalized definition (enabled activities, criterion scale/target/direction/weight/dimension mapping, dimension sources, overall components/missing policies, band keys), excluding labels/branding. Keep the mapping snapshot to explain incompatibility; a conflict never silently discards stored adjustments.
- [ ] Test finite values, configured nonzero minimum, /10 dimension, target durations, zero-weight defensive handling, missing/disabled keys and migration up twice on a disposable database. Run domain tests; expect green. Commit only these files.

## Task 2: One effective scoring path with exact no-correction parity

**Files:** Modify `app/services.py`, `app/routes_admin.py`, `app/report_exports.py`; create `tests/test_management_correction_scoring.py`.

**Consumes:** Model/map/signature from Task 1 and existing submission/admin/general aggregates.
**Produces:** Result rows with unchanged effective field names plus `automaticScore`, `automaticColor`, `manualColor`, per-activity/per-dimension automatic score and `manuallyGraded`; criterion breakdown exposes raw evaluator average, automatic average, effective average and adjustment marker.

- [ ] Capture unmodified snapshots for fictional rating, target, nonstandard-scale and missing-policy fixtures. Add failing tests applying an in-memory correction record, expecting changed effective score and byte-identical evaluator/admin records.

```python
def assert_original_records_unchanged(before, after):
    assert after['evaluation_submissions'] == before['evaluation_submissions']
    assert after['submission_versions'] == before['submission_versions']
    assert after['admin_evaluations'] == before['admin_evaluations']
```

- [ ] Run the new scoring tests and record the expected failure. Keep fixtures local through existing conftest.
- [ ] Batch-load correction rows once in `result_snapshot`. Preserve all current arithmetic/rounding for an unaffected activity/dimension. For affected criteria apply the normalized correction before weighting; recompute affected activity /5 and dimensions using Decimal. Do not round each criterion to evaluator input steps. Preserve original automatic computation separately rather than deriving raw values by subtracting corrections.
- [ ] Use effective scoring coverage for exclude-missing overall calculations: a dimension can be score-ready when every contributing criterion has automatic data or a correction. Keep evaluator expected/submitted/complete and missing counters unchanged; show the distinct manual coverage. Pass this derived scoring readiness into existing `overall_score`, not fake submission completion.
- [ ] Recompute configured ranks from effective numerical scores; overall aggregation still includes only completed Journees. Automatic color follows effective numeric score; a valid manual band overrides display only. Expose signature/band conflicts explicitly in profile/results and do not reinterpret saved values.
- [ ] Use the same normalized criterion values for profile breakdown contributions. Original evaluator values/raw Sport measurements remain visible. Export effective score/color with automatic values and adjustment provenance; raw evaluator sheets remain raw. General Assessment stays on its existing versioned/audited autosave path.
- [ ] Test overlap (activity=4 then shared dimension=2), missing criteria, nonzero-minimum scales, target scores, existing admin precedence, unchanged snapshot fields without overrides, configured ties, color-neutral ranks, profile/table/export agreement and one batch query regardless of recruit count. Run tests and existing scoring/dual-rank/export suites; expect green. Commit.

## Task 3: Atomic preview/save/restore/history APIs

**Files:** Modify `app/schemas.py`, `app/correction_service.py`, `app/routes_viewer.py`, `app/routes_admin.py`; create `tests/test_management_correction_api.py`.

**Consumes:** Shared calculation and domain from Tasks 1–2; existing require_results/require_admin, CSRF, tenant context and `audit`.
**Produces:** Both admin and viewer routes at `/journeys/{journey_id}/recruits/{recruit_id}/corrections`: GET state/history, POST `/preview`, PUT apply. No production data is touched by tests.

```python
class ManagementCorrectionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    configurationSignature: str
    action: Literal['set', 'restore', 'undo']
    level: Literal['criterion', 'activity', 'dimension', 'color']
    key: str
    activityKey: str | None = None
    value: Decimal | None = None
    reason: str = Field(default='', max_length=2000)
    eventId: str | None = None
```

- [ ] Write failing API tests: valid preview has no write/audit; PUT updates effective values; stale revision/signature gets 409; unprivileged/wrong-tenant/CSRF requests fail without a write.
- [ ] Validate allowed combinations explicitly: numeric set requires finite value and target; color set requires configured key and no numeric value; restore only selected target; undo requires same-recruit audit event. Never accept an arbitrary caller-supplied map for undo.
- [ ] Preview returns affected criteria and before/after automatic/effective grades/totals, missing and replaced adjustments, current revision/signature, and warnings. It uses the shared snapshot calculation with a request-local override argument, not temporary persistent writes.
- [ ] Apply checks scoped Journey/Recruit, revision and configuration; performs conditional update `WHERE revision = expected`, increments once, and records one audit event in the same transaction. Unique-key conflict on two revision-0 inserts returns 409. On any error roll back both record and audit.
- [ ] Restore clears only the resolved target criteria (or color). Undo restores the chosen event's before-map/color only if current revision matches. Audit captures actor/time, scope, request, before/after map and effective totals, configuration and reason. Include event ID in history for undo.
- [ ] Re-read current inputs on apply; if automatic scores/config changed since preview, return a fresh preview requiring reconfirmation rather than applying an outdated before/after display. Add an `inputFingerprint` from preview to the strict request contract for this comparison; omit it only on preview, require it on PUT.
- [ ] Test concurrent first save, concurrent color/numeric saves, atomic injected audit failure, scope isolation, bad event ID, restore/undo history, config label change versus scale change, nonfinite payloads and restart persistence. Run tests; expect green. Commit.

## Task 4: Clean, shared management grade/color editor

**Files:** Create `app/static/management-corrections.js`; modify viewer/admin JavaScript, CSS and asset versions; create `tests/test_management_correction_browser.py`.

**Consumes:** Profile correction state and preview/apply contracts from Task 3.
**Produces:** `correctionEditorHtml(profile)` and `bindCorrectionEditor(root, {profile, apiBase, request, reload})` shared by management and admin profiles.

- [ ] Write browser tests against disposable data first: open Edit grade, select target, enter 4, preview lists each contributing criterion at 4/5, Save changes, reload, inspect immutable raw evaluations, restore, change color without numerical changes.
- [ ] Add an expandable Grade corrections section to the profile. Keep existing evaluation inspection actions intact. Use a compact level/target selector, labelled configured-scale numeric input, optional reason and explicit Preview/Apply changes buttons. Show Automatic and Effective separately; disclose raw evaluator details using existing breakdown UI.
- [ ] Show preview changes and overlap/missing-grade warnings inline. Disable double submit while pending; keep user input on error/offline; 409 stops saving and offers Reload latest. Escape/Cancel closes without a write. Keyboard focus and 390px layout must work without nested modals or page overflow.
- [ ] Add configured color selector with current automatic color, Manual marker and Restore automatic color. It uses the same preview/apply/revision flow. No hard-coded LRC options. General Assessment's existing autosave/conflict controls are untouched.
- [ ] After successful changes refetch profile and result data, invalidate only request-local/client result state and redraw ranks. Never restore stale pre-save arrays. Render audit actor/time/reason/before/after and Restore/Undo action with clear scope.
- [ ] Test slow save, network failure, concurrent conflict, long criterion names, deleted band/config conflict, empty grades and General Assessment interaction. Run desktop/mobile browser captures in one batch, fix once, confirm once; run Impeccable detector once at completion. Commit.

## Task 5: Rehearsal, review and sequential two-site rollout

**Files:** Modify `app/main.py` expected revision to `0019_management_corrections`, migration tests and `docs/ai/HANDOFF.md`; add `docs/ai/MANAGEMENT_CORRECTIONS_ROLLOUT.md`.

**Consumes:** Fully tested Tasks 1–4. **Produces:** reviewed release and recorded deployment/parity evidence, or a clearly stated rollout blocker.

- [ ] Run non-browser suite, then browser tests serially (the test fixture database is shared). Check JavaScript syntax, diff whitespace and Docker build through existing CI. Do not run production load tests or repeat old performance investigations.

```powershell
python -m pytest -m 'not browser' -q --tb=short
$env:RUN_PLAYWRIGHT='1'
python -m pytest tests/test_playwright_workflow.py tests/test_sitewide_browser.py tests/test_management_correction_browser.py -q --tb=short
node --check app/static/viewer.js
node --check app/static/admin.js
node --check app/static/management-corrections.js
git diff --check
```

- [ ] Obtain fresh protected database backup and verify restore into a disposable local database, not another provider database. Capture pre/post immutable-table parity with existing `scripts/capture_parity_snapshot.py`; account for only the new empty correction table. Require exact existing score/rank/export parity before enabling edits.
- [ ] Verify current deployments/health and exact schema head; rehearse 0018→0019 locally. Because Oregon's old readiness code checks an exact revision, document and minimize the schema-check transition; do not claim zero interruption. Block rollout if an active Journee is running or a verified backup is unavailable.
- [ ] Complete independent code review under the selected execution workflow; fix data integrity, authorization, scoring and concurrency findings with failing-then-passing tests.
- [ ] Push the exact reviewed release SHA, wait for CI, migrate once with TLS verification and one DB connection, then deploy main/Oregon sequentially within Aiven's connection budget. Do not overlap deployments, rename services or alter paid plans.
- [ ] Verify both health endpoints, matching assets, schema revision, read-only authenticated workflows and score parity. Use only a disposable test workspace if a live correction smoke test is needed; never alter real recruit grades as a test.
- [ ] Record rollback: before any correction exists the prior app can use the additive schema after its readiness revision compatibility is handled; after corrections exist keep/export the layer and use a forward fix or correction-aware previous build. Do not silently deploy old scoring that ignores corrections.
- [ ] Update HANDOFF with files, tests, schema, exact SHAs, deployments, remaining limitations and no-change infrastructure statement. Commit documentation.

## Plan review

All approved design areas map to Tasks 1–5. The small rank refinement is independently approved and handled separately, with no schema dependency. Recommended execution: native implementation in this worktree with a fresh whole-branch reviewer; these tasks share scoring contracts, so keeping one implementer reduces interface drift. Review this plan before starting the new correction layer.
