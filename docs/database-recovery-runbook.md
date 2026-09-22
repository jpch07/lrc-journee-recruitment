# Evalday database recovery and migration

This procedure does not restore access to a provider-disabled cluster. A fresh,
readable source or a verified recent full backup is a prerequisite. Never silently
replace newer event records with the August backup to make the website respond.

## What is backed up

`scripts/database_transfer.py` exports **every application table and every column**,
including raw answers, decimal grades, comments, notes, accounts, managed passwords,
permissions, configuration versions, audit records, session rows, idempotency rows,
inline photo bytes, R2 keys, and image checksums. IDs are unchanged. The current
schema uses UUIDs, not sequences; unexpected sequences or unrecognized tables and
columns cause a safe stop rather than being dropped from a backup.

The archive is a typed ZIP/JSONL format, not `pg_dump` format. Export takes one
read-only database snapshot. Per-table counts and full-row SHA-256 digests are
verified before a destination import commits. PostgreSQL import is transactional,
requires an empty application schema, and does not merge or overwrite records.
The deployment lock is operational state and is not copied. Alembic revision is
preserved in the manifest and destination revision table.

Timestamp parity follows Evalday's UTC convention: SQLite may return a naive
datetime even for a timezone-enabled column, so exports interpret naive datetimes
as UTC and normalize all aware datetimes to UTC. This preserves the represented
instant across SQLite and PostgreSQL instead of treating an added `+00:00` offset
as a data change. It is not suitable for a separate application's naive local-time
database without an explicit timezone conversion first.

**Security:** Archives contain personal information and currently contain readable
managed passwords. They are not encrypted by this utility. Store them in a private
encrypted backup location, never in Git, email, a public bucket, or a shared folder.
Retain the Render environment configuration securely and separately; values are
not included in the archive. In particular retain the session secret, admin hash,
sheet configuration, and R2 credentials. R2 photo objects must be backed up and
checksum-verified separately: relational object metadata is not a photo backup.

`scripts/backup_r2_photos.py` downloads the configured private R2 bucket without
changing any remote object. It checks each object's stored SHA-256 when available,
uses conditional ETag reads, rechecks the bucket inventory, and independently
verifies the completed local archive. Object keys never become filesystem paths.
It refuses to overwrite an existing archive and stops if the configured size
budget is exceeded. A partial archive without `manifest.json` is not a backup.
Set the four `LRC_R2_*` variables securely and choose a private, non-synced output
directory; the archive is not encrypted. This does not recover any photos that
remain inline in an inaccessible database.

## Read-only inventory and full export

Provide the source connection string through `EVALDAY_SOURCE_DATABASE_URL`, never
as a command argument. Use PostgreSQL/Cockroach TLS verification and the correct
CA on the machine running the command.

```powershell
python scripts/database_transfer.py inspect
python scripts/database_transfer.py export data/backups/fresh-production.zip
```

Export refuses to overwrite an existing path. An interrupted archive lacks its
final manifest and cannot be restored. The existing small `*-cutover-*.json`
files produced by `capture_parity_snapshot.py` contain hashes/counts only; they
cannot restore records.

## Rehearsal and cutover

1. Keep the main Render database URL unchanged. Set up a separately named empty
   Aiven test database and supply its URL in `EVALDAY_TARGET_DATABASE_URL`.
2. Validate a dry run, then restore. Do not expose this destination to live app
   traffic while restoring: the full-row parity proof is for a quiescent target.

```powershell
python scripts/database_transfer.py restore data/backups/fresh-production.zip --initialize-empty
python scripts/database_transfer.py restore data/backups/fresh-production.zip --initialize-empty --confirm
python scripts/database_transfer.py verify data/backups/fresh-production.zip
```

3. Rehearse login, workspace switching, completed results/Excel, photos, attendance,
   room plans, assignments, evaluation saves/submissions and admin overrides. Test
   with 20 participants and 30 evaluators in a separate synthetic test database.
   Measure errors, latency, connections, actual database size and projected growth.
4. Rehearse a reverse export from Aiven and restore into an empty PostgreSQL
   database (Neon uses PostgreSQL). Compare all digests and application results.
5. Stop accepting writes for the actual maintenance window. Capture a new final
   source archive and photo backup. Restore that final archive into the empty
   production destination and compare all rows. Never reuse an older rehearsal
   archive after the source accepted additional writes.
6. Change only the primary Render database URL once tests and parity pass. Keep
   other secrets and R2 settings unchanged. Deploy and verify authenticated
   production health and workflows. Retain the old source and all backups.
7. Do not downgrade/delete the old provider until the new production system is
   accepted and a complete rollback backup is verified.

## Rollback

After cutover the old source becomes stale as soon as Aiven accepts writes.
Freeze writes, export the latest Aiven rows with this utility, and restore into
an empty compatible PostgreSQL database. Verify hashes and photos before switching
Render back. A simple environment-variable switch to a stale source loses data
and is not a valid rollback. No rollback tool can recover rows never backed up
from a provider that has disabled all SQL access.

## Independent account-only fallback site

Use a **fresh source**, not an unconfirmed old snapshot. The exact workspace slug
selects one workspace, all its configuration versions/access-profile definitions,
its evaluator directory and accounts (including hashes, managed passwords and
workspace-wide permission flags), and associated owner/platform accounts. No
journeys, recruits, scores, session tokens, audit rows, directory participants,
attendance links or journey-specific permissions are copied. Old attendance
permissions intentionally have no meaning without their old events.

```powershell
python scripts/database_transfer.py export data/backups/fallback-account-seed.zip --workspace-slug lrc-journee-recruitment-2026
python scripts/database_transfer.py restore data/backups/fallback-account-seed.zip --initialize-empty --allow-workspace-seed
python scripts/database_transfer.py restore data/backups/fallback-account-seed.zip --initialize-empty --allow-workspace-seed --confirm
```

This archive is marked `workspace-seed`, not `full`, and ordinary restore refuses
it unless explicitly authorized by `--allow-workspace-seed`. Seed only a separately
provisioned, empty fallback database. Review copied configuration for external
sheet links and give the new host an independent session secret, monitoring and
storage configuration. No provider accounts or host deployments are created by
this tool. Existing account/password authentication remains unchanged, but all
users must sign in anew because sessions are excluded.

## Backup-source inventory as of this audit

- `data/backups/*.json`: parity-only manifests, not full logical backups.
- Local SQLite snapshots and `outputs/production-backups`: August snapshots,
  predating later production changes; unsuitable as an undisclosed replacement.
- `event-day-watchdog.yml`: health checks only, no database backups.
- CI and Hugging Face deployment workflow: deployment/testing, no data backups.
- No fresh automated logical backup is established by these repository files.

Provider-managed backups may exist, but they must actually be recovered through
an authorized provider mechanism before claiming recovery. Check creation time
and expected recent events/accounts; a backup's existence is not proof of freshness.

## Limits and safeguards

Aiven Free is finite and not an uptime guarantee. Preserve enough disk headroom,
keep all photos out of normal row queries, cap connection pools below the service
limit, and alert on failed authenticated readiness, low capacity and missing
backups. A second independently prepared site is a fallback, not live replication:
new evaluations recorded only on one site do not appear on the other automatically.

`scripts/check_storage_usage.py --json` reports physical current-database storage
for PostgreSQL, including indexes and TOAST. Only Aiven hosts receive a default
1,000,000,000-byte (1 GB) budget; other PostgreSQL hosts require an explicit
`--database-allowance-bytes` or `EVALDAY_DATABASE_ALLOWANCE_BYTES`. Reduce this budget
to reserve room for other databases, WAL and service overhead, which the current-
database measurement cannot capture. Cockroach output is explicitly an approximate
logical-row estimate, not a physical quota measurement. Query failures return
nonzero and unknown usage, never a false zero/OK. The tool does not send email or
measure request-unit quotas; external monitoring and provider alerts remain required.
