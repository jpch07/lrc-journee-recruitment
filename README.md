---
title: LRC Journee Recruitment
sdk: docker
app_port: 7860
---

# LRC Journee Recruitment

Digital administration and evaluator system for Lebanese Red Cross recruitment Journees.

## What is included

- Independent Journee workspaces and lifecycle management.
- Saved recruit/evaluator rosters and attendance with arrival times.
- Searchable recruit master directory synchronized from the approved Google Sheet, with phone and birth-date autofill.
- Reproducible room and evaluator assignment previews with explicit publication.
- Mobile evaluator tasks for Sport, Escape Room, Negotiation, Skills, and Simulation.
- Fixed 2026 dimension-weighted rubric, live monitoring, six-dimension profiles/rankings, activity statistics, comments, and exports.
- Versioned submissions, audit events, private recruit photos, and browser/server drafts.

The production application is at `https://lrc-journee-recruitment.onrender.com`. The admin
application is served at `/admin`. The permanent evaluator URL at `/evaluate` and its QR code
automatically resolve the single Active Journee. PostgreSQL remains authoritative in production. The Google Sheet
is used only as the source for the cached recruit directory; Journee attendance and all operational data stay in
PostgreSQL, and no persistent container filesystem is used.

## Local run

Python 3.11 is required.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
$env:LRC_JOURNEE_ADMIN_PASSWORD = "change-me"
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`. Without `LRC_DATABASE_URL`, development data is stored in
`data/journee.db`. Production must set an external PostgreSQL URL.

For the standard local setup used during this project (port 8001, password `lrcadmin`, and
development testing tools enabled), run:

```powershell
.\scripts\run_local.ps1
```

### Evaluation simulator

When `LRC_JOURNEE_TEST_TOOLS=true` and `LRC_JOURNEE_ENV` is not `production`, Activity Control
shows a development-only Evaluation simulator. Open an activity, explicitly activate the tool,
then choose either all remaining assigned evaluations or an exact number. The simulator creates
valid randomized forms using the normal scoring, submission-version, monitoring, results, and
audit paths. Existing submitted evaluations are not overwritten.

The simulator is forcibly unavailable in production even if its feature flag is accidentally set.
Use it only with fictional or disposable Journees because generated submissions affect results.

Create a complete fictional roster, room plan, and published assignment set for local review:

```powershell
python scripts/seed_demo.py --confirm
```

The local default password is `admin` only when no password variable or hash is supplied. Never
use that fallback in a shared or production environment.

## Production secrets

- `LRC_DATABASE_URL`: existing external PostgreSQL connection string.
- `LRC_JOURNEE_ADMIN_PASSWORD_HASH`: Argon2 hash for the shared admin password.
- `LRC_JOURNEE_SESSION_SECRET`: at least 32 random bytes/characters.
- `LRC_JOURNEE_COOKIE_SECURE=true`: required on HTTPS production.
- `LRC_GITHUB_ACTIONS_TOKEN`: private token allowed to dispatch and cancel Actions workflows in
  `jpch07/lrc-journee-recruitment`. It is used only by authenticated admin event-day controls and
  is never returned to the browser.
- `LRC_RECRUIT_SHEET_ID`: public Google Sheet ID for the master recruit directory.
- `LRC_RECRUIT_SHEET_NAME`: source tab name (defaults to `List of Recruits`).
- `LRC_RECRUIT_SHEET_SYNC_SECONDS`: cache refresh interval (defaults to 300 seconds).

Generate an Argon2 password hash locally:

```powershell
python scripts/hash_password.py
```

Production PostgreSQL tables are created in the `journee_recruitment` schema. The app never
uses the web-service container filesystem as authoritative storage.

All database revisions run through Alembic at startup. PostgreSQL deployment holds an advisory
lock while migrating, preventing two deployment processes from racing the same schema.

## Tests

```powershell
python -m pytest -m "not browser"
$env:RUN_PLAYWRIGHT = "1"
python -m playwright install chromium
python -m pytest -m browser
```

Unit/integration coverage includes rubric totals, scoring boundaries, duration parsing, room and
pairing constraints, optimistic concurrency, evaluator isolation, idempotent submission, and the
complete Sport workflow. The browser test launches the real application and verifies the mobile
layout. CI runs both test groups and a Docker build.

## Render deployment (free production runtime)

The production service is described by `render.yaml`. Connect this GitHub repository as a Render
Blueprint and supply the two `sync: false` secrets when prompted. Render generates the session
secret itself. Confirm the free instance plan before creating the service.

The service uses external PostgreSQL for all confirmed data, so a Render restart or replacement
does not lose Journees, photos, assignments, or evaluations. From a Journee's Settings & audit
page, choose 6 or 12 hours and press `Start Journee protection`. The authenticated server starts
the redundant GitHub monitors; no manual Actions setup is required on event day.

## Hugging Face deployment (optional)

If the Hugging Face account supports Docker Spaces, create a separate Space (recommended name:
`lrc203/journee-recruitment`), copy the
secrets above into its Settings, then run:

```powershell
$env:HF_TOKEN = "..."
python scripts/deploy_huggingface_space.py --repo-id lrc203/journee-recruitment
```

The deploy script requires `huggingface-hub` from `requirements-dev.txt`. GitHub Actions workflows
are included for test/build and production deployment; configure the `HF_TOKEN` repository secret
and the production Space secrets before enabling automatic deploys.

### Transfer existing local Journees

The local SQLite database is intentionally excluded from images and source control. To preview a
copy of selected Journees into an empty production schema, set `LRC_DATABASE_URL` locally and run:

```powershell
python scripts/migrate_sqlite_to_postgres.py `
  --journey "2 August - Journee 2026 AM" `
  --journey "2 August - Journee 2026 PM"
```

Review the selected Journees and row counts, then repeat with `--confirm`. The transfer preserves
operational and audit history but excludes browser/admin sessions and idempotency records. It
refuses to write if the destination already contains a Journee.

## Operational references

- [Architecture and data flow](docs/ARCHITECTURE.md)
- [Event-day runbook](runbooks/EVENT_DAY.md)
- Health endpoints: `/health/live` and `/health/ready`
- Local health command: `python scripts/check_health.py <base-url>`
