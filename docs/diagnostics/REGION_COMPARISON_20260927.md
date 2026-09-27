# Aiven exchange comparison: Virginia versus Oregon

Completed 2026-09-27, approximately 19:25:57–19:30:59 UTC. This executes the bounded comparison previously prepared in SITEWIDE_RELEASE.md. It is not a recruitment application deployment or page benchmark.

## Result

| Measurement | Virginia median / p95 | Oregon median / p95 | Samples per region |
|---|---:|---:|---:|
| Warm sequential SELECT 1 exchange | 66.714 / 67.087 ms | 28.492 / 28.720 ms | 60 |
| New connection establishment | 516.599 / 818.695 ms | 212.071 / 220.190 ms | 3 |

Warm median fell **57.3%** (38.222 ms per exchange). Warm batch medians were Virginia 66.446, 67.011, 66.714 ms; Oregon 27.041, 28.503, 28.599 ms. Zero measurement failures. Nearest-rank p95; the connection p95 is the maximum of only three samples and is exploratory.

The controlled comparison supports placing the application in Oregon while retaining the existing Aiven service. It does not support changing the database provider. It does not prove a particular page-load improvement, resolve Free idle sleep, measure PostgreSQL CPU time, or measure the client-to-Oregon route.

## Method and safeguards

- Same standalone revision `bdc853b04ec9799f4cd7abe5a14dc94560cf285c`, Python 3.11.11, psycopg binary 3.2.9 in both regions, native Free web services. Root `tools/region_probe`; no recruitment application imports, startup/lifespan, migrations, seeding, or assessment data queries.
- Same existing Aiven endpoint, independently matched by successful verify-full TLS with the service-page project CA. CA DER SHA256: `df855add8c0a3c6d82ec69dc01c4a2d3d64d71a14341fc0ad2b20d641c902a66`. TLSv1.3 in all batches. SSL verification was never weakened.
- Existing Render CLI deployment authentication accessed the documented service environment-variable API inside a local process. Only the required database URL was transferred to the probe secret configuration. No raw environment responses, connection strings, passwords, browser cookies/tokens, or Render credentials were printed/committed. No new database credentials, roles or permissions.
- Temporary configuration under `C:\Users\jeanp\AppData\Local\Codex\lrc-region-probe-20260927`, outside repositories/OneDrive. Protected directory ACL restricted access to the current user. Render secret-file upload carried the URI and CA directly; no credentials in process command arguments or public HTTP output.
- Three alternating pairs: Virginia, Oregon, Virginia, Oregon, Virginia, Oregon. One fresh connection per batch; 20 warm calls reuse it, with synchronous calls, no pipeline context and automatic preparation disabled. Connection establishment separately times DNS/TCP/TLS/authentication/session setup, not merely TCP handshake.
- Session startup sets default_transaction_read_only=on; each batch explicitly begins READ ONLY and verifies transaction_read_only. Fixed SQL allowlist: SELECT 1, read-only mode, TLS state and aggregate client-connection counts/reserved limits. No recruitment tables read or written. Read-only enforced in every batch, including catalog checks.
- Connect timeout 5 seconds, statement/lock timeout 1 second, idle transaction timeout 5 seconds; child-process wall deadline 35 seconds. Fixed 60-second alternating slots and missed-slot rejection. All connections closed; minimum observed inter-region gap 55.185 seconds. Measurement span 302.411 seconds, within the global 30-minute deadline (creation began around 19:20:57 UTC; cleanup around 19:31 UTC).
- An initial slow HTTP readiness exchange triggered the conservative clock guard before any DB batches. Repeated warm checks showed both clocks within 0.61 seconds of local time; unchanged controller then proceeded. HTTP status/results endpoints only read cached sanitized data and cannot invoke SQL. They are not a public SQL runner.
- Current connection preflight: 3 client connections including the check, maximum 20, 3 superuser-reserved slots, role limit -1. Conservative headroom 14 during the check and every measurement batch. This is current usage, distinct from the maximum. The check connection closed before regional measurements. Final check found zero remaining named probe sessions and closed itself.

Warm timings are client-driver exchange wall time inside each region, including network/protocol/database waits, not PostgreSQL CPU. SELECT 1 isolates a small exchange; it is not representative application SQL execution work. Different hosting providers/network paths remain part of the comparison. Three short batches reduce ordering bias but cannot establish hourly/daily p95, peak-load behavior or long-term stability. Historical production app_ms/sql_ms and simulated SQLite samples are not comparable metrics and were not combined into a speedup claim.

## Cost and cleanup

Preflight authenticated Render Billing: Hobby; no card; $0 accrued/projected; remaining 269.3/750 Free hours, 498/500 pipeline minutes and 3.62/5 GB bandwidth, 23 included service slots. This workspace-wide budget was ample for two short, fixed probes; no paid plan/upgrade/chargeable feature was selected. Both Free deployments finished in under one minute from creation. Official no-payment-method overage behavior is suspension/build disabling, not a supplementary purchase: https://render.com/docs/free . No additional spending authorized or incurred.

Created ONLY:

| Region | Temporary service | Temporary deployment |
|---|---|---|
| Virginia | srv-dasmp6fpn0mc7393mn50 | dep-dasmp6vpn0mc7393mou0 |
| Oregon | srv-dasmp78u01pc73cl4ieg | dep-dasmp7gu01pc73cl4kl0 |

Both services deleted by their recorded IDs after identity/ownership/root/plan checks; API GET returned 404 for each afterward. Six workers reported closed connections; final read-only catalog check verified zero probe sessions. Local controller exited; no background monitor remains. Temporary URI, CA, controller/cleanup scripts and directory removed, with absence verified. No downloaded CA file was found in the narrowly checked Downloads CA filenames after the failed browser download. Original application secrets/CLI configuration untouched.

Sanitized raw batch samples, summary, preflight, created/deleted resource ledger and closure check preserved at sibling `lrc-region-comparison-evidence-20260927/`. Source retained in `tools/region_probe`. Focused mock checks covered read-only rejection, pressure rejection, TLS configuration, 20-query bound and closure; compilation and actual standalone builds passed. Completed recruitment application tests/reviews were not rerun.

## Reversible cutover proposal — NOT executed or approved

Smallest infrastructure change supported by this evidence: move application execution to Oregon, keep Aiven/provider/region/data and the tested application behavior unchanged. This needs separate approval for a full Oregon app service, startup policy, connection budget and public URL routing. No database migration is needed for a region change.

1. Resolve public identity first. Render does not support changing an existing service's region in place (https://render.com/docs/regions); each service receives its own onrender.com URL (https://render.com/docs/your-first-deploy). Do not assume renaming transfers `evalday.onrender.com`, and never delete the current service to free that name. If the exact official origin must remain, obtain a supported hostname-routing solution or explicitly approve an authenticated, tested gateway on the existing service. That gateway adds a cross-region hop and another Free instance's hours; cookie/CSRF/forwarded-host/redirect behavior and spend limits need validation before approval. Alternatively approve a new Oregon canonical URL and an explicit GET-only entry redirect from the old origin; do not silently redirect write requests across origins. Existing host-scoped sessions would require sign-in at the new origin. A custom-domain alternative requires separate domain/DNS approval and cannot be assumed cost-free.
2. Before launching any full app against production, review and guard startup effects. The current lifespan calls initialize_database (CREATE SCHEMA/advisory migration lock/Alembic upgrade) and ensure_platform_owner followed by commit. A standby is not automatically read-only just because schema revisions match. First implement and fixture-test an explicitly approved migration/bootstrap-free startup mode that verifies the existing revision/configuration, or obtain explicit approval for the identified startup effects. No startup mode was added in this experiment. No shadow writes, production mutation acceptance tests or migrations are implied by cutover approval.
3. Securely transfer only required app secrets directly between approved local/API processes, preserving signing/session secrets, tenant configuration and CSRF semantics. Do not rotate roles or secrets as part of relocation. Use the existing database with verified TLS. Inventory actual pool overrides and legacy sharing before app duplication; defaults alone are insufficient. Limit overlap/rollout concurrency so old/new app pools, legacy, admin and reserved capacity fit the observed headroom. Do not increase pools. Disable auto-deploy and previews on the new app; deploy an exact tested SHA.
4. Keep Virginia `srv-da61170u01pc738qvcag` at `258638de459d7b3091797e6e27d85ecd4aaa067a` / `dep-dasm2a0jo6nc73cd2jug` available until the routing/startup gates pass. No database restore/downgrade. Verify Oregon readiness and bounded authorized read-only navigation, including no-init activity flows only where independently proven. Measure complete client-visible flows before accepting the routing switch; this experiment does not replace that check.
5. After explicit approval, switch only the agreed URL/routing layer. Monitor a short bounded error/readiness window. If a clear regression occurs, immediately restore routing to the retained Virginia deployment above, confirm readiness/normal read-only navigation, then stop/remove only the new Oregon app when appropriate. Do not restore stale data or delete assessment history. Keep rollback secrets/connection access available securely until that window closes; recheck included hours/bandwidth before permanent two-service routing.

Current production was verified afterward unchanged: exact SHA/deployment above, main linked, auto-deploy OFF, Free/Virginia. Legacy, settings, pools, domains and recruitment data unchanged. Free idle sleep remains a separate decision.


## Subsequent approved phase
User subsequently approved one Free Oregon full application with a new URL, existing Aiven and opt-in startup safety. Executed without repeating this probe experiment. Oregon final386cb0dec265df9402b2113842b46c90a6f8023d /dep-dasnl917lnhs73a07vrg /https://evalday-oregon.onrender.com; Virginia retained unchanged. Actual navigation evidence, safeguards and verification limits in ../ai/OREGON_RELEASE.md. The earlier unapproved cutover proposal above remains historical; no hostname transfer/proxy/redirect/DNS change occurred.
