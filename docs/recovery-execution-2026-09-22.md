# Recovery execution ledger — 22 September 2026

Scope: execute the already approved database-recovery runbook and independent
fallback setup; configure the approved Brevo sender and recipient. No paid plans,
no changes to the ambulance project, no replacement of current data with stale
snapshots. Source authority remains Cockroach until a controlled final cutover.

## Bounded implementation

Add a manual, five-minute SMTP delivery-verification workflow using repository
secrets, then exercise the existing alert state machine. No outage is induced on
production. Two messages use a clearly labelled test hostname. SMTP acceptance is
not claimed as inbox receipt. Existing monitor schedules remain unchanged.

- New workflow test observed failing because the workflow did not exist.
- Implemented workflow; 22 monitoring tests passed.
- Fresh independent review found partial recipient rejection incorrectly reported
  as success and insufficient workflow-runtime coverage.
- Added runtime success/outage-failure/recovery-failure tests and a recipient
  rejection test. Observed rejection test fail before fixing the sender.

## Decisions

- Ruling: use the supplied migration runbook and this ledger, not unavailable
  superpowers helper scripts; their referenced subskills are not installed.
  Verification still requires real tests and explicit evidence.
- Ruling: follow the user's explicit request to continue unattended for the
  bounded delivery test rather than pause for a duplicate design approval.
- Do not create a supposedly independent backup on the main Aiven service, nor
  use expiring Render Postgres or a paid/unknown-plan database as a substitute.
- Do not switch a live service to the staged snapshot after new source writes.
  Freeze both known source-connected hosts, take a new source snapshot, and
  restore into a fresh database before routing production to Aiven.
