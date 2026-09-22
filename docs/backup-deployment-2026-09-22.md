# Independent backup deployment — execution ledger

Plan: `docs/database-recovery-runbook.md`, independent account-only fallback.
User approved separate free hosting/database accounts and copying LRC accounts
and configuration without historical events. Main website remains untouched.

- Saved backup URI parsed without displaying its secret and targets a different
  Aiven service. Backup Render credential resolves to a different owner workspace.
- Fresh read-only source seed exported at 19:32 UTC on 22 September: 80 accounts,
  zero journeys. Empty-target dry run passed; transactional import and post-commit
  full-row verification passed, digest
  `328b3c5b481be0ca83ddb973b4091936f07362747b83fa7a963a8b74600e702f`.
- Initial diagnostic used `count` instead of the manifest's `rows` field and
  stopped before importing; corrected the diagnostic and reused the validated
  archive. No import retry overwrote records.
- Ruling: provision the already approved independent backup using existing
  deployment code; no redesign or new application subsystem. The helper subskills
  referenced by executing-plans are unavailable; use this ledger and direct tests.
- Ruling: leave R2 credentials unset on the backup. Existing deferred inline-photo
  fallback stores new compressed images in its own Aiven DB. This avoids sharing
  the main R2 quota; cost: images consume the backup DB's finite storage allowance.
- Ruling: duplicate the existing watchdog configuration with a separate target
  variable and cache prefixes. Reusing the main watchdog unmodified would silently
  monitor the wrong host. SMTP sender and GitHub repository remain shared; hosting,
  database, sessions and photo storage are separate. Shared provider-wide outages
  remain possible.
- Bounded configuration under the user's existing continue-without-approval
  instruction: same checked-in monitoring program, no application behavior edits.
- TDD: backup-workflow test failed on missing file before configuration was added.
  Test asserts exact equivalence to the established watchdog apart from names,
  backup target and separate cache prefixes.
- Deployment explicitly requests Render `plan: free`; API defaults are not used.
  Use a fresh session secret; preserve existing account hashes and permissions.

- Render created `srv-dapdhanf3r2c73cbrlng`, Frankfurt, Free, URL
  `https://evalday-backup.onrender.com`. Deployed application commit `1faeb96`.
  Explicit `LRC_GITHUB_WORKFLOW=backup-event-day-watchdog.yml` and repository
  variable `RENDER_BACKUP_APP_URL` points to the backup. Main target is unchanged.
- Ruling: keep backup automatic deployments disabled, preserving its validated
  application revision when the main website changes. Cost: future backup
  application updates require a deliberate deployment and verification.
- Live HTTP checks passed: existing managed admin login, empty library, create
  temporary event/recruit, attendance save, photo upload/download/ETag 304,
  general assessment autosave/readback, results and Excel download, logout.
  Removed only the generated verification event; final event library is empty.
- Monitoring tests: 27 passed. Full non-browser regression suite: 277 passed,
  one browser test deselected. Existing main watchdog file remains unchanged.
- Fresh independent review: no actionable code findings. Reviewer did not read
  secrets or perform remote tests. Remaining release gate: publish workflow on
  default branch and verify a real backup-targeted monitor run.
