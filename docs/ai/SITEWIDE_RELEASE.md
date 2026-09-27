# Site-wide release candidate (not deployed)

## Scope and safety
Preserves configurable organizations, event-day polling, permissions, scoring/ranks, published distributions and histories. A/C1/C2 included; temporary diagnostics omitted. No migration. Source worktrees unchanged.

## Infrastructure decision (prepared only)
Read-only Render tools confirmed evalday Free/Virginia/one instance/main/auto-deploy OFF and diagnostic deploy dep-dasfbte0tbcc73f5so6g live at 46bde68. Legacy also Free/Virginia/one instance/main; auto-deploy ON, previews OFF. Isolated branch pushes cannot trigger those main deployments. Repository deployment workflows require workflow_dispatch; push only runs tests/build. Do not merge to main: that would deploy legacy unless separately addressed with explicit authorization.

Region experiment needs separate approval. Prefer bounded read-only TLS probes from Virginia and Oregon with a dedicated read-only role, verify-full CA and fixed deadlines. No second app startup, lifespan, migrations, seed or authentication flows. Sample sequential SELECT 1 (e.g. 20 warm executions), fixed harmless catalog read, connection handshake separately; record median/p95 and failures. One connection per region, no simultaneous app tests/load. Stop at connection pressure/error threshold and destroy approved probe infrastructure afterward. Approval must identify probe infrastructure, cost cap, duration, read-only role/access mechanism, and cleanup. Do not execute in this task.

Connection budget: production database limit reported as 20, not observed active count. Source live defaults: each process pool_size=5, max_overflow=0; one uvicorn process in Docker CMD. During replacement old+new could reserve 10. If legacy shares Aiven and has the same configuration it adds 5, leaving 5 total for admin/probes/other clients; two simultaneous probes leave only 3. Legacy concurrent replacement would add another 5 and consume the entire budget before probes. Actual deployed pool overrides, legacy database identity, and other clients are NOT exposed by current get_service read tool; defaults are not proof of runtime values. Before approval execute a separately authorized nonsecret settings inventory and read-only connection-budget check; do not read/print credentials. Avoid experiments during deployment. No pool increase proposed.

Always-on choices verified against Render docs/pricing 2026-09-27: 0.5c-512mb (legacy Starter) $7/month; 1c-2g (legacy Standard) $25/month. Free spins down after 15 minutes idle. Paid compute avoids free idle sleep; app changes do not. Suggested lowest change is Starter on existing evalday in Virginia, retaining Aiven; Standard only if resource measurements justify it. Requires explicit approval for paid plan/charges and the deployment caused by applying it; auto-deploy must stay OFF. Region decision is separate.
Sources: https://render.com/docs/free ; https://render.com/docs/compute-plans ; https://render.com/pricing

## Release and rollback
Approval boundary: exact candidate commit deployment on evalday only, no main merge, no auto-deploy change, no legacy changes. Verify production with authorized passive/read-only navigation; no sample participants, room mutations or seeding. Preserve timing evidence and expiry (2026-09-28 00:00 UTC stops collection only).
Rollback: exact previous live baseline 1faeb9647624995aed3373e474a70f641bc1fa6f, or current diagnostic 46bde68 when appropriate while its bounded capture is still intended. Code-only rollback; no database downgrade or deletion of assessments/audit entries. Local changes are separable commits and can be reverted in order. Prefer base bec5bc5 when keeping newer backup isolation in a future main-based release. Deployment approval must choose the exact rollback revision.

## Verification
Windows Python 3.11.3, disposable SQLite, Node v24.19.0, real Chromium/Edge fallback. At application checkpoint dfed815:
- `python -m pytest -m "not browser" -q`: 303 passed, 2 deselected in 91.50s.
- `RUN_PLAYWRIGHT=1 python -m pytest -m browser -q -s`: 2 passed, 303 deselected in 58.60s (zero simulated delay). Includes mobile admin/configuration/assessment acceptance and linked site-wide flow with draft navigation/save checks.
- Separate 67 ms simulated cursor delay browser comparison: baseline 1 passed in 32.93s; final candidate 1 passed in 33.74s.
- `python -m pytest tests/test_sitewide_performance.py::test_http_flow_measurement -q -s`: 1 passed in 2.34s; archived baseline measure: 1 passed in 3.58s. Initial local baseline open measured 45 SQL; latest archived run measured 44. Table uses the latest run.
- Initial integrated run: 2 failed, 300 passed, 2 deselected in 95.63s; both stale static/query-count expectations were diagnosed and repaired before passing integrated run. Focused repair: 15 passed in 8.76s.
- Final stale room-copy response guard has separate focused tests (see lrc-final-race-tests.log) and will be included in branch CI.
- `git diff --check` clean. Local `docker version --format '{{.Server.Version}}'` failed because Docker Desktop daemon is unavailable. Isolated branch CI runs the actual Docker build; check the branch's Test and build workflow before release.
Full local logs preserved outside repository at sibling `lrc-sitewide-evidence-dfed815/`. These are new runs, distinct from historical user-provided C1/C2/diagnostics test results. Usage budget unavailable.

## Reproducible local evidence
Baseline app revision cb2eae8 is origin/main plus reviewed A/C1/C2, unpacked with git archive into OS TEMP. Candidate application checkpoint 3ce066b (later dfed815 changes measurement tests only). Fixture: fictional linked assignments/submissions/full version histories, multiple activities, two rooms; additional tests create a second tenant and revoke sessions. Default pool and SQLite test database; no production data or requests. SQL counts include cursor statements and operation initialization writes, exclude PostgreSQL ping and SET LOCAL. Request counts cover the measured UI flow. Timings are single samples, not statistically significant p95 or Aiven measurements.

| Flow | Baseline HTTP requests | Candidate | Baseline SQL/checkouts | Candidate SQL/checkouts |
|---|---:|---:|---:|---:|
| Open Journee/dashboard | 2 | 1 | 44 / 6 | 29 / 2 |
| Room activity load | 5 | 1 | 56 / 15 | 16 / 3 |
| Edit published | 7 | 1 | 90 / 22 | 19 / 3 |

Real Chromium browser, same fictional fixture, simulated 67 ms sleep before each SQLite cursor execution (not network or production/Aiven timing): opening 3554.1 -> 2715.3 ms; initial sport activity tab 1586.8 -> 1676.9 ms; switch to escape_room 1529.4 -> 1405.6 ms; click Edit published to editable controls 4233.7 -> 1959.4 ms. Room edits save through existing mutation/CSRF/version/audit machinery; published payload verified unchanged. Initial tab latency did not improve in this sample; do not claim uniform latency resolution.

Preserved production evidence from user handoff, NOT collected again: dashboard app/sql/cursor count (ms): 3042.867/2570.460/35; 3081.725/2628.394/35; 3013.688/2560.917/35; 3085.837/2629.041/35. Readiness 404.965 ms app, acquire 201.769, ping 67.119, revision 67.189, handler 403.727, 3 measured statements. Shared workspace/runtime durations approximately 0.67?0.75 s. SQL includes network/DB waits; phases overlap; acquire is not pure pool queueing. Region latency remains a hypothesis until controlled comparison. Peak returned 60-second CPU sample 0.026066134 of 0.15 and memory 105398270 of 536870900 bytes, not an exclusion of brief spikes. No exact Edit published endpoint timing exists in generic production admin_api records.

## Prompt 2 release gate — 2026-09-27
Approval was for exact 9200b14de4b209aec0c099c52995060d480c116c on evalday only. GitHub run 36338820565 was rechecked: exact SHA, successful non-browser, browser and Docker-build steps. Existing results reused, not rerun. Application changes since the earlier completed Sol/High review, including the stale room-copy guard, received one bounded read-only Sol/High review.

That review blocked deployment: saving one working editor discarded the other editor's unsaved draft; Apply could publish stored data while unsaved edits remained visible. The isolated repair tracks rooms/assignments separately, preserves the other DOM draft, adopts acknowledged edit revisions, and blocks plan-changing/publication actions with pending drafts or saves. Backend authorization/configuration, worker sessions, schema, historical data and assessment autosave code are unchanged. Focused Node-backed regression tests cover both save orders, 409 preservation, publication blocking and acknowledgement. The real-browser regression exercises both editors and consecutive revision-aware saves using disposable fictional SQLite data.

Release boundary rechecked before any deploy: workspace tea-d9q4t9egekts73cg8ri0; evalday srv-da61170u01pc738qvcag; live dep-dasfbte0tbcc73f5so6g at 46bde687cb26a0dc2c53cd7ee1f397dae4e3c5d5. Linked branch main, auto-deploy OFF, Free/Virginia unchanged. No service, production data or main changes. Authenticated Render CLI supports explicit --commit and --wait; generic connector deploy lacks commit selection and must not be used. Browser inventory failed to load its request-header policy; authenticated production navigation was not established.

No migration/model/dependency/Dockerfile differences between the approved candidate and live diagnostic revision. September 22 full-row verification documents provide historical independent backup evidence, not a fresh snapshot guarantee; no secrets or backup exports were accessed. No deployment or production performance checks occurred because the application release gate failed. New application SHA requires renewed user approval after final-head CI. Exact authorized code-only rollback target supersedes the earlier options above: dep-dasfbte0tbcc73f5so6g / 46bde687cb26a0dc2c53cd7ee1f397dae4e3c5d5. No data restoration, downgrade or deletion is authorized.

Focused local logic check: python -m pytest tests/test_assignment_loading.py tests/test_assessment_autosave.py tests/test_frontend_polling.py tests/test_browser_smoke.py -q — 11 passed in 5.70s (Windows Python 3.11, Node, disposable SQLite). Final browser and final-head CI results are recorded in Git notes on the repaired commit and sibling release-gate evidence logs. Deployment remains pending renewed exact-SHA approval. Idle sleep and geographic latency remain unresolved; no paid upgrade or region experiment approved.
