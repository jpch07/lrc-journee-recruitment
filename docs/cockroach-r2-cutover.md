# CockroachDB and R2 cutover runbook

Production is not switched until the two parity manifests match and photo
verification succeeds. The existing Neon database remains the rollback source.

## One-time setup

1. Create a CockroachDB Basic cluster and SQL user. Keep its connection string
   private. The application converts a Cockroach Cloud PostgreSQL URL to the
   required `cockroachdb+psycopg` SQLAlchemy dialect automatically.
2. Create a private Cloudflare R2 bucket and an object read/write API token.
3. Add these secret values to a staging Render service:
   `LRC_DATABASE_URL`, `LRC_R2_ENDPOINT_URL`, `LRC_R2_ACCESS_KEY_ID`,
   `LRC_R2_SECRET_ACCESS_KEY`, and `LRC_R2_BUCKET`.
4. Enable Cockroach Cloud Basic Request Unit notifications and storage
   notifications at 50%, 75%, and 90%. Run `scripts/check_storage_usage.py`
   daily for independent database logical-byte and R2 object-byte checks.

## Rehearsal

1. Back up Neon with `pg_dump` and store the encrypted file outside Render.
2. Apply Alembic through revision `0017_room_evaluator_locks` to a disposable copy.
3. Run `capture_parity_snapshot.py outputs/parity-source.json` against the source.
4. Set `LRC_SOURCE_DATABASE_URL` to the source and `LRC_DATABASE_URL` to the
   empty Cockroach cluster. First run `migrate_postgres_to_cockroach.py`, then
   repeat with `--confirm`.
5. Run `migrate_photos_to_r2.py` against Cockroach without deletion, then with
   `--remove-inline` after all checksums pass.
6. Capture `outputs/parity-target.json`. The relational table hashes will differ
   only for `recruits` after intentional removal of inline photo bytes; all row
   counts and every other table hash must match. Verify each recruit's stored
   `photo_sha256` against R2.
7. Run the full test suite plus the complete staging Journee workflow.

## Production maintenance window

1. Close operational editing and take a final Neon backup.
2. Apply migrations through revision 0017 to Neon and upload photos to R2 **without** removing the
   inline Neon copies.
3. Capture the source manifest and migrate relational rows into an empty
   Cockroach schema.
4. Verify target photos, then remove only Cockroach inline photo bytes.
5. Switch the five Render secrets atomically and deploy this revision.
6. Check `/health/live`, `/health/ready`, admin login, evaluator login, recruit
   attendance, room planning, one draft evaluation, one photo, results, and an
   Excel report.
7. Keep Neon paid and untouched until owner acceptance.

## Rollback

1. Stop writes or enable the maintenance page.
2. Export the latest Cockroach rows and restore them to a fresh Neon schema so
   no post-cutover edits are lost.
3. Change only `LRC_DATABASE_URL` back to the restored Neon connection. R2 can
   remain active because photo object keys are database-independent.
4. Redeploy, verify both health endpoints and the authenticated workflows, then
   reopen access.
