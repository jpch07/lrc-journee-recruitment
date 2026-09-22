# Optional health email alerts

Email delivery is **not activated or verified** merely by deploying this code.
With absent or incomplete settings the guard prints `Email alerts: disabled or
not configured; no email will be sent.` No provider was configured by these changes.

To enable it, an owner must configure an existing approved SMTP sender using the
following GitHub repository **secrets**, then verify a real test message reaches
the intended mailbox (including checking spam). Never put credentials in repository
variables, source files, reports, or workflow inputs.

- `EVALDAY_SMTP_HOST`
- `EVALDAY_SMTP_PORT` — optional; defaults to 587. Port 465 uses implicit TLS.
- `EVALDAY_SMTP_USERNAME`
- `EVALDAY_SMTP_PASSWORD`
- `EVALDAY_ALERT_FROM` — an address authorized by the SMTP provider.
- `EVALDAY_ALERT_TO` — the receiving email address.

The other ports use mandatory STARTTLS, with certificate and hostname verification.
There is no plaintext fallback. The monitoring jobs run outside Render; an SMTP
provider's free allowance and sending restrictions must be checked separately.
The tool never purchases service or changes a provider account.

## Alert behavior

- An outage alert is attempted after the health probe retries fail.
- Recovery is reported when both health endpoints succeed again.
- Repeated unhealthy polls do not send repeated delivered alerts.
- Failed email delivery does not stop monitoring. It retries at most once every
  five minutes while that transition remains relevant; no credentials or SMTP
  server response text are printed.
- The first healthy reading is not a recovery and sends no email.
- The redundant secondary event-day lane has email disabled.

The baseline and primary event-day lanes save small, **non-secret** health state
files using GitHub Actions caches. Deduplication is best effort per lane/state,
not globally exactly once. Baseline and primary monitors may independently report
the same outage. Missing/evicted caches, overlapping runs or a killed job can also
cause duplicate alerts. SMTP acceptance is not proof of delivery to the inbox.

The five-minute baseline has a configured HTTP retry budget of 140 seconds:
two endpoints × (three 20-second attempts + two 5-second pauses). Email, checkout,
and cache operations take additional time; the job retains its five-minute cap.
GitHub schedules can be delayed, so this is not instant or guaranteed monitoring.
Long event-day jobs notify during the run rather than waiting for the segment to end.

For a controlled deployment test, first confirm the selected SMTP account is
approved and free within its allowance. Use a non-production test endpoint and
observe one deliberate outage and its recovery. Verify both emails and confirm
that several repeated unhealthy checks produce only one delivered outage alert.
Until this end-to-end test succeeds, describe email monitoring as **unverified**.

The manually dispatched `test-monitor-email.yml` workflow sends two test messages
using the hostname `outage-recovery-test.invalid`. It exercises the actual
notification state machine (outage, repeated outage, recovery, repeated recovery)
without touching the production website or its monitor state. Any SMTP failure or
recipient refusal fails the test. A successful workflow proves SMTP acceptance,
not inbox delivery; the recipient must still check the inbox/spam folder.
