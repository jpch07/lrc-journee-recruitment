# Event-day runbook

## Before the Journee

1. Verify the external PostgreSQL backup completed and that a recent restore test exists.
2. Open the production service early enough to wake it from sleep, then run:

   ```powershell
   python scripts/check_health.py https://lrc-journee-recruitment.onrender.com
   ```

3. Confirm `/health/live` and `/health/ready` both return `200`.
4. Open the Journee, set the room count in Rooms & Assignments, then verify the roster, evaluator roles, and mandatory room placements.
5. Confirm attendance on a second admin device and test the evaluator QR with a fictional evaluator if available.
6. In GitHub Actions, manually run `Event-day health monitor` for the expected event duration. It
   checks both endpoints every five minutes and keeps the free instance awake.

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
- Use the current Google Sheets process only as the agreed pilot fallback; do not enter real data into two authoritative systems after cutover.

## After the Journee

1. Close every activity and mark the Journee Completed.
2. Export the full Excel workbook and verify its rosters, raw answers, scores, rankings, comments, and audit tabs.
3. Store the export according to LRC policy. Export the admin-only photo ZIP only when required.
4. Confirm the next database backup. Archive the Journee only after operational review.
