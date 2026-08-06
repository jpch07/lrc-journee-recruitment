# Architecture and data flow

## Runtime

One FastAPI/Uvicorn Docker container on a free Render web service serves JSON APIs and two static JavaScript applications. The
admin and evaluator interfaces use separate HttpOnly session cookies, CSRF tokens, and API
namespaces. PostgreSQL uses the dedicated `journee_recruitment` search path; SQLite is only a local
fallback. Alembic startup migrations are serialized with a PostgreSQL advisory lock.

## Operational state

- `journeys` isolates every event and owns its lifecycle. The permanent evaluator route resolves the single Active Journee.
- `activity_states` identifies the one evaluator-visible assignment round for each activity.
- Rosters and confirmed attendance live in `recruits` and `evaluators`; browser-side attendance
  drafts are never sent until Save is pressed.
- Versioned room plans and assignment rounds are `preview`, `published`, or `superseded`. Only the
  published IDs referenced by activity state are evaluator-visible.
- Evaluation submissions are unique per assignment. Their current value is stored with exact
  numeric score and JSON answers; every save has a `submission_versions` snapshot.
- General assessment and audit tables preserve profile grading and sensitive administrative
  changes. Idempotency records make repeated submit taps safe.

## Assignment sequence

Sport uses the complete confirmed-present roster without rooms. Escape Room publishes the fixed
room membership. Negotiation solves a new evaluator/recruit pairing inside those rooms. Skills
then generates a global pairing across all present recruits and evaluators, and Simulation creates
an exact copy of Skills. The min-cost flow objective covers every recruit,
minimizes historical pair repeats, favors Overall primaries, distributes second evaluations, and
uses a stable seed for reproducible tie-breaking. During evaluator shortages it expands balanced
primary loads and disables secondary assignments.

Room membership freezes after Escape Room starts. A reasoned override creates a new published plan
for future rounds; existing assignments, submissions, and history stay unchanged.

## Score calculation

Weighted rubrics use exact `Decimal`/PostgreSQL `NUMERIC` arithmetic. Activity scores average the
submitted evaluator scores; a missing co-evaluator is flagged but does not dilute the available
score. Sport normalizes its four raw exercises against fixed targets and caps each at five.

The overall result uses six dimensions, each normalized to /1. Willingness, Respect,
Adaptability, and Intelligence combine their weighted criteria across Escape Room, Negotiation,
and Simulation. Application is the Skills score divided by five; Physical Ability is the Sport
score divided by five. General is the average of punctuality, Respect to Us, and seriousness.
Overall /20 is `(sum of six dimensions + General) × 20 / 7`. Ranking is shared competition rank
on unrounded values; color thresholds also use unrounded values.

## Security boundaries

- Admin sessions require the shared Argon2-protected password and a display name recorded in audit.
- Evaluator sessions are scoped to one selected evaluator and the single Active Journee. The permanent route
  uses name-only selection, which cannot prevent evaluator impersonation.
- Assigned-photo endpoints re-check the evaluator, current published round, and assignment before
  returning private bytes. Evaluators cannot call admin routes or access rankings/comments.
- Non-active Journees reject new and existing evaluator sessions; changing the Active Journee invalidates old sessions.
- Confirmed writes use transactions, CSRF validation, optimistic versions, and idempotency where
  repeated taps are expected.

## Production durability

All confirmed records and compressed WebP photos are in external PostgreSQL. The web-service container may
restart or lose its filesystem without losing operational data. `/health/live` checks the process;
`/health/ready` verifies connectivity and the expected Alembic revision. See the event-day runbook
for wake-up, backup, monitoring, incident, and export procedures.
