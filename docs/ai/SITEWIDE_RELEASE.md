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
Pending integrated checkpoint. Logs are in OS TEMP outside repository; do not treat prior user-provided test summaries as newly run tests. Local Docker daemon initially unavailable; branch CI will perform the real Docker build after isolated push.
