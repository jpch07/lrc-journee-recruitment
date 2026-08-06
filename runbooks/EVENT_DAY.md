# Event-day runbook

## Before the Journee

1. Verify the external PostgreSQL backup completed and that a recent restore test exists.
2. Open the production admin application and the Journee being operated.
3. Go to Settings & audit > Event-day protection, select `6 hours` or `12 hours`, and click
   `Start Journee protection`. This also makes the selected Journee Active.
4. Wait for the status to change from `Starting` to `Active`. The application verifies the process,
   database, and two offset external monitor lanes automatically.
5. Set the room count in Rooms & Assignments, then verify the roster, evaluator roles, and mandatory room placements.
6. Confirm attendance on a second admin device and test the evaluator QR with a fictional evaluator if available.
7. A separate scheduled check runs every five minutes at all times. This is a backup; GitHub notes that
   scheduled workflows can be delayed.
8. Freeze production during the event: do not merge/deploy code, change hosting configuration, rotate
    secrets, or restart Render until the Journee is completed unless responding to an active incident.
9. Absolute zero downtime cannot be guaranteed by a free single-instance host. Keep the current
    Google Sheets process and a fresh Excel export available as the agreed emergency fallback.

## During the Journee

1. Save attendance explicitly; verify the present counts on the dashboard.
2. Generate a preview, inspect warnings and manual changes, then publish. A preview is never evaluator-visible.
3. Open only the activity being performed and watch received/expected submissions.
4. Close the activity when evaluation time ends. Reopening requires an audit reason.
5. For Escape Room, publish rooms before assignments. Negotiation rematches inside those rooms. Skills rematches globally across the full Journee; Simulation copies Skills.
6. If a frozen room must change, generate a new preview and use `Publish override`; record the operational reason. It changes future rounds only.

## Incident handling

- If evaluator connectivity is intermittent, completed fields remain in local browser storage and server drafts retry after interaction.
- Do not publish a new assignment merely to refresh evaluator screens. Use Refresh; clients poll every five seconds.
- If an evaluator/recruit becomes unavailable, save corrected attendance, generate a replacement preview, review warnings, and publish it.
- If Render restarts, confirmed data remains in PostgreSQL. Re-run both health checks before continuing.
- If the protection card shows `Incident`, click `Restart protection`. This cancels the unhealthy run and
  starts a replacement for the selected 6- or 12-hour duration. If the admin application itself cannot
  open, tell Codex `Incident on Journee <name>; restore protection` so hosting, database, and monitor state
  can be checked from outside the application.
- Use the current Google Sheets process only as the agreed pilot fallback; do not enter real data into two authoritative systems after cutover.

## After the Journee

1. Go to Settings & audit > Event-day protection and click `End Journee`. After confirmation, this stops
   the external monitors, closes every open activity, locks evaluator edits, and marks the Journee Completed.
2. Export the full Excel workbook and verify its rosters, raw answers, scores, rankings, comments, and audit tabs.
3. Store the export according to LRC policy. Export the admin-only photo ZIP only when required.
4. Confirm the next database backup. Archive the Journee only after operational review.
