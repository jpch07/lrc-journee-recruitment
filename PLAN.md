# LRC Journee Recruitment Digital System

## Summary

Build one responsive web application with two isolated interfaces:

- An admin workspace for Journee creation, attendance, rooms, assignments, monitoring, rankings, profiles, exports, and audit history.
- A mobile evaluator workspace reached through one permanent `/evaluate` link/QR code that resolves the single Active Journee. Evaluators select their name, see only their assignments, complete evaluations, and may edit submissions until the activity closes.

The implementation will reuse the ambulance-check architecture documented in [README.md](<D:/jeanp/OneDrive - American University of Beirut/Programming/Codex/LRC Auto fill check QR/README.md:12>):

- Python, FastAPI, Pydantic, HTML/CSS/JavaScript, and Docker.
- A separate public Hugging Face Docker Space under the existing `lrc203` account.
- The existing external PostgreSQL connection pattern through `LRC_DATABASE_URL`, using a dedicated `journee_recruitment` schema.
- No operational dependency on Google Sheets. Excel/CSV exports remain Google Sheets-compatible.

Hugging Face supports FastAPI through arbitrary [Docker Spaces](https://huggingface.co/docs/hub/main/spaces-sdks-docker). No authoritative data will be kept on the Space filesystem because its normal disk is ephemeral across restarts; all data and compressed photos go to PostgreSQL. [Hugging Face storage documentation](https://huggingface.co/docs/hub/spaces-storage)

## End-to-End Journee Workflow

### 1. Journee library

The first admin page behaves like the requested “Word files” analogy:

- Cards or rows for every Journee, showing name, date, status, attendance, active activity, completion, and last update.
- Actions: open, create blank, duplicate, rename, archive, and export.
- Statuses: `Draft`, `Ready`, `Active`, `Completed`, and `Archived`.
- Each Journee is completely isolated. Opening another Journee never changes the previous one.
- Any Journee can be permanently deleted after an explicit destructive confirmation; archiving remains the recoverable option.
- Confirmed changes save immediately. Unsaved attendance/profile edits show a warning before navigation.

### 2. Setup

For each Journee, admins:

- Enter its name, date, and number of rooms.
- Add recruits individually or import them from Excel/CSV.
- Add an optional recruit phone number.
- Upload recruit photos individually, from a phone camera, or by bulk matching filenames to full names.
- Add evaluators individually, import them, or reuse them from a master evaluator directory.
- Set every evaluator’s role to `Overall` or `Dossard`.
- Configure mandatory evaluator placements per room.
- Review the fixed 2026 rubric; admins cannot change its criteria, weights, or sport targets.

### 3. Attendance

Recruit attendance and evaluator attendance have separate tabs.

Recruit rows contain:

- Photo, name, phone number, Present/Absent status, and arrival time.
- Marking Present proposes the current Asia/Beirut time, but the time remains editable.
- Changes remain browser-side drafts until `Save attendance` is pressed.
- `Discard changes` restores the last confirmed state.
- Arrival time does not automatically set the punctuality grade.

Evaluator rows contain:

- Name, Overall/Dossard role, Present/Absent status, and mandatory-room indicator.
- The same explicit Save/Discard behavior.
- An absent mandatory evaluator produces a room-planning warning.

Removing someone after assignments exist deactivates them instead of deleting history. The current published state remains visible until a corrected attendance list and replacement assignment are confirmed.

### 4. Activity lifecycle

Only one activity is operationally open at a time, except Skills and Simulation, which may be opened and closed together:

1. Attendance is confirmed.
2. Admin generates an assignment preview.
3. Admin reviews warnings and may make manual swaps.
4. `Publish assignments` atomically replaces the evaluator-visible assignment.
5. Admin opens the activity.
6. Evaluators receive the change automatically within approximately five seconds or use Refresh.
7. Admin monitors submissions live.
8. Admin closes the activity, locking evaluator edits.
9. An admin may reopen it with a required reason.

Sport is independent of rooms and can be run separately. The room-based sequence is:

- Escape Room: create and publish the fixed room plan and first room pairing.
- Negotiation: keep the same rooms and generate a new pairing within each room.
- Skills: leave the room constraint and generate a new pairing globally across all present recruits and evaluators.
- Simulation: reuse the global Skills pairing exactly; no redistribution occurs.

Recruits and evaluators remain room-bound for Escape Room and Negotiation only. Skills is global and Simulation reuses Skills.

## Pages and User Experience

### Admin pages

1. **Admin unlock**
   - One shared password stored as a Hugging Face secret.
   - Required admin display name after unlocking.
   - The display name is written to audit events but is not a verified identity.

2. **Journee library**
   - Create, open, duplicate, rename, archive, and export Journees.

3. **Live dashboard**
   - Present recruit/evaluator counts.
   - Overall/Dossard availability.
   - Active activity and room status.
   - Submission progress, unresolved warnings, and quick actions.
   - Live activity averages and provisional overall ranking.

4. **Attendance**
   - Separate recruit and evaluator tabs with explicit Save/Discard.
   - Search, filters, add/import/deactivate, photo upload, and arrival-time editing.

5. **Rooms and assignments**
   - Room-count and mandatory-evaluator setup.
   - Room cards listing recruits, evaluators, roles, capacity, and warnings.
   - Assignment tabs for Sport, Escape Room, Negotiation, Skills, and Simulation.
   - Preview, regenerate, manual move/swap, repeat-pair warnings, and Publish.
   - Published and draft versions displayed separately.

6. **Activity control and monitoring**
   - Activity cards with `Not started`, `Open`, and `Closed` states.
   - Recruit view: expected submissions versus received submissions, with filters for incomplete recruits.
   - Evaluator view: assigned recruit(s), submission status, and submitted time.
   - Evaluation-detail drawer showing criteria, calculated score, comments, and edit history.
   - Admin correction after closure requires a reason and creates a new version.

7. **Results**
   - One tab per activity with rank, grade /5, completion count, and activity average.
   - Overall ranking with score /20, color label, missing-item count, and completion state.
   - Missing expected submissions remain visible and count as zero.
   - Export current table or full Journee.

8. **Recruit profile**
   - Search/select a recruit.
   - Photo, attendance, arrival time, overall /20, overall rank, and Red/Yellow/Green label.
   - Per-activity grade /5, rank, submission completion, evaluator breakdown, and comments.
   - Editable punctuality, respect, and seriousness fields from 0.0 to 1.0 in 0.1 increments.
   - General admin comment with Save/Discard.
   - Full audit and correction history.

9. **Settings, audit, and export**
   - Journee metadata, permanent evaluator link/QR, archive, and duplication.
   - Audit log for attendance saves, publications, manual overrides, corrections, closures, and profile changes.
   - Full Excel/CSV export containing rosters, attendance, rooms, assignments, raw answers, scores, comments, rankings, and audit events.
   - Optional photo ZIP export restricted to admins.

### Evaluator pages

1. **Journee landing**
   - Journee name/date and searchable evaluator-name list.
   - Name-only access, as selected.
   - The permanent evaluator link resolves only the single Journee marked Active; name-only access cannot prevent one evaluator impersonating another.

2. **Evaluator home**
   - Evaluator name, Overall/Dossard role, room, and current activity.
   - Assigned recruit card(s), including the photo only for assigned recruits.
   - Activity cards showing Upcoming, Start, Draft, Submitted/Editable, or Closed.
   - Automatic assignment refresh plus a visible Refresh button.

3. **Evaluation form**
   - Recruit photo/name, activity, evaluator, room, and completion counter.
   - Criterion cards with dimension, title, explanation, and grade input.
   - Competency grades accept 0.0–5.0 in 0.1 increments.
   - All required criteria must be completed; comments are optional.
   - Drafts save locally immediately and to the server after a short debounce.
   - Explicit submission confirmation prevents accidental saves.
   - Submitted forms remain editable until the admin closes the activity.
   - A successful submission returns the evaluator to remaining tasks.

4. **Sport form**
   - Push-ups: non-negative whole number.
   - Durations accept seconds, `MM:SS`, `HH:MM:SS`, `1m30`, `1m 30s`, or similar unambiguous unit forms.
   - The parsed duration and calculated /5 score are shown read-only for verification.
   - Invalid, negative, or ambiguous values are rejected.

The evaluator interface never exposes rankings, other evaluators’ answers, unassigned recruits, or admin comments.

## Assignment Engine

### Room generation

- The admin enters `X` rooms.
- Present recruits are randomized reproducibly and divided so room sizes differ by at most one.
- Mandatory present evaluators are placed first.
- Every other present evaluator is assigned to a room.
- The optimizer first tries to give each room enough evaluator capacity for its recruits, then spreads Overall evaluators proportionally and as evenly as possible.
- Surplus evaluators remain in a room as visible standby evaluators.
- Duplicate mandatory placements, empty staffed rooms, absent mandatory evaluators, and rooms with insufficient capacity are reported before publication.
- Admin manual moves are allowed before Escape Room publication.
- Once Escape Room starts, room membership is frozen; a later override requires a reason and affects future rounds only.

### Pairing rules

Use a deterministic min-cost maximum-flow solver, seeded per preview, so generation is fast, reproducible, and testable.

Hard rules:

- Only confirmed-present recruits and evaluators participate.
- Escape Room and Negotiation pair only people in the same room.
- A recruit receives at most two evaluators.
- Every recruit receives one evaluator before any recruit receives a second.
- Simulation copies Skills assignments.
- A published assignment changes only through another confirmed publication.

Priority order:

1. Cover every recruit with a primary evaluator.
2. Avoid evaluator–recruit pairs used in Sport, Escape Room, Negotiation, or Skills.
3. Maximize primary assignments to Overall evaluators.
4. Use remaining evaluators as second evaluators, prioritizing Dossards, up to two per recruit.
5. Distribute second evaluations fairly across recruits.
6. Use seeded random tie-breaking to avoid systematic alphabetical bias.

Normal evaluator capacity is one recruit per activity. Shortage behavior is the selected exception:

- If a room or global activity has fewer evaluators than recruits, evaluator loads increase only enough to cover every recruit once.
- Extra recruits are balanced as evenly as possible.
- Overall evaluators receive priority within the balanced workload.
- No second-evaluator assignments are generated during a shortage.
- In a room activity, every extra recruit assigned to an evaluator remains inside that evaluator’s room.

The solver first attempts a zero-repeat solution. If constraints make that impossible, it minimizes the number of repeated pairs and explains every forced repeat in the preview. Skills-to-Simulation reuse is intentional and never reported as a violation.

## Scoring and Rankings

### General rules

For Escape Room, Negotiation, Simulation, and Skills:

\[
\text{Evaluator activity score /5}
= \sum(\text{criterion grade} \times \text{criterion weight})
\]

- Store calculations as PostgreSQL `NUMERIC`, not floating-point values.
- Preserve raw values and display scores to two decimals.
- Comments do not affect scores.

For a recruit with one or two published evaluator assignments:

\[
\text{Recruit activity score}
= \frac{\text{sum of submitted evaluator scores}}{\text{number of submitted evaluator scores}}
\]

A missing co-evaluator submission remains visibly incomplete but does not add a zero to the average. An activity with no submitted evaluation receives zero; a recruit with no valid assignment receives zero and a critical warning.

The activity average is the mean score of all confirmed-present recruits, including their missing-submission zeros. Absent recruits are excluded.

### Sport

For each evaluator:

\[
\text{Exercise score}=\min\left(\frac{\text{result}}{\text{target}}\times5,\ 5\right)
\]

Targets:

- Push-ups: 30 repetitions.
- Wall sit: 120 seconds.
- Beep test: 600 seconds.
- Plank: 120 seconds.

The four scores are averaged equally and the evaluator’s Sport score is rounded to two decimals. Results above target cap at 5.

### Overall /20

Let `D₁…D₆` be Willingness, Adaptability, Respect, Intelligence, Application, and Physical Ability,
each normalized to /1, and:

\[
G = \frac{\text{punctuality}+\text{respect}+\text{seriousness}}{3}
\]

where each general grade is /1. Willingness, Adaptability, Respect, and Intelligence combine their
weighted criteria across Escape Room, Negotiation, and Simulation. Application is Skills /5,
normalized to /1, and Physical Ability is Sport /5, normalized to /1. The formula is:

\[
\text{Overall /20}
=
\frac{20}{7}
\times
(D_1+D_2+D_3+D_4+D_5+D_6+G)
\]

Its maximum is exactly 20. The five activity grades /5 and activity rankings remain available as
separate operational statistics but do not directly determine the overall score.

Missing dimension inputs or general grades count as zero. A missing co-evaluator is flagged as
incomplete but does not dilute a grade that another assigned evaluator submitted. The interface
always shows how many components are missing.

Color grading uses the exact unrounded value:

- Red: `< 13`
- Yellow: `≥ 13 and < 16`
- Green: `≥ 16`

Activity and overall rankings use shared competition rank based on unrounded scores: `1, 2, 2, 4`.

### Fixed 2026 rubric

Escape Room source: :codex-file-citation{path="D:/jeanp/OneDrive - American University of Beirut/LRC/Recruitment journee 2026/2026 Evals.xlsx" purpose="source" artifact_kind="workbook" sheet="Escape Room" range="A4:E12"}

| Dimension | Criterion | Weight | Explanation |
|---|---|---:|---|
| Willingness | Active participation and contribution | 15% | Engaging continuously with the team during puzzle solving rather than withdrawing |
| Willingness | Initiative and task division | 15% | Proactively dividing tasks based on individual strengths |
| Respect | Communication and active listening | 15% | Sharing clues clearly, listening to team ideas, and avoiding talking over others |
| Respect | Self-control and composure | 10% | Staying calm, supportive, and focused despite time limits and locked-room pressure |
| Adaptability | Time management under pressure | 10% | Maintaining steady focus and momentum without freezing or panicking |
| Adaptability | Setback recovery and resourcefulness | 10% | Pivoting strategies smoothly when a proposed answer fails or when stuck |
| Intelligence | Logical problem solving and pattern recognition | 15% | Efficiency in analyzing supplied clues |
| Intelligence | Common sense prioritization | 10% | Using common sense to order priorities by severity |

Negotiation source: :codex-file-citation{path="D:/jeanp/OneDrive - American University of Beirut/LRC/Recruitment journee 2026/2026 Evals.xlsx" purpose="source" artifact_kind="workbook" sheet="Negotiation" range="A4:E12"}

| Dimension | Criterion | Weight | Explanation |
|---|---|---:|---|
| Willingness | Initiative and persistence | 15% | Staying engaged and trying supportive approaches through difficult or resistant responses |
| Willingness | Team support and coordination | 10% | Backing up teammates without interrupting or sending conflicting messages |
| Respect | Empathy and active listening | 10% | Validating concerns with warmth rather than clinical force |
| Respect | Non-aggressive communication | 15% | Maintaining a calm tone and avoiding hostile or pushy language |
| Adaptability | Adaptable to dynamic patient behavior | 15% | Pivoting smoothly when patients change their objections or become emotionally heightened |
| Adaptability | Self-preservation and safety awareness | 10% | Recognizing safety boundaries during high-risk escalations |
| Intelligence | Clarity of reasoning and persuasion | 15% | Offering simple, reassuring options |
| Intelligence | Understanding boundaries | 10% | Knowing when to negotiate and when to pause for safety and seek help |

Simulation source: :codex-file-citation{path="D:/jeanp/OneDrive - American University of Beirut/LRC/Recruitment journee 2026/2026 Evals.xlsx" purpose="source" artifact_kind="workbook" sheet="Simulation" range="A4:E12"}

| Dimension | Criterion | Weight | Explanation |
|---|---|---:|---|
| Willingness | Commitment and effort under difficulty | 15% | Sustaining physical effort, grip, and enthusiasm throughout the simulation |
| Willingness | Team cohesion and synchronization | 15% | Coordinating movements during lifting, carrying, and patient placement |
| Respect | Composure under distraction | 15% | Maintaining focus and calm execution despite pressurizing elements |
| Respect | Patient empathy and reassurance | 10% | Providing compassionate communication during care and transport |
| Adaptability | Bystander/distraction management | 10% | Handling distracting elements without losing focus on patient care |
| Adaptability | Recovery and dynamic adjustment | 10% | Adjusting smoothly when obstacles or sudden scenario changes occur |
| Intelligence | Retention and application of taught skills | 10% | Applying bandage and stretcher techniques accurately |
| Intelligence | Role assignment and task clarity | 15% | Assigning clear roles before taking action |

Skills source: :codex-file-citation{path="D:/jeanp/OneDrive - American University of Beirut/LRC/Recruitment journee 2026/2026 Evals.xlsx" purpose="source" artifact_kind="workbook" sheet="Skills" range="A4:D13"}

The dedicated Skills sheet contains nine criteria and is authoritative; the combined sheet omits “Composure and precision.” Since the workbook contains no Skills explanations, use the explanations already present in the [current evaluator application](https://script.google.com/macros/s/AKfycbyCTQ3nyH8jv5zcSTlbD4JsnPeccilP3XSZ7XBJnw2aYOYmqofO7cuBNRxdlF2Ervqk/exec).

| Theme | Criterion | Weight | Explanation |
|---|---|---:|---|
| Brancardage | Posture and lifting technique | 15% | Correct body posture and safe lifting technique |
| Brancardage | Physical capability and carrying ability | 15% | Ability to carry and maneuver the stretcher safely |
| Brancardage | Team synchronization and command timing | 10% | Coordinating movements and following commands at the correct time |
| Brancardage | Communication and encouragement | 10% | Communicating clearly and encouraging teammates |
| Brancardage | Grasping of concepts | 10% | Understanding and applying the concepts taught |
| Bandage | Correct placement and coverage | 10% | Placing the bandage correctly and covering the required area |
| Bandage | Tension and stability | 10% | Maintaining suitable bandage tension and stability |
| Bandage | Efficiency and speed | 10% | Completing the technique efficiently without sacrificing accuracy |
| Bandage | Composure and precision | 10% | Remaining calm and precise while applying the bandage |

Sport source: :codex-file-citation{path="D:/jeanp/OneDrive - American University of Beirut/LRC/Recruitment journee 2026/2026 Evals.xlsx" purpose="source" artifact_kind="workbook" sheet="Sport" range="A4:D8"}

Obvious spelling mistakes in the workbook are corrected in display text without changing meaning or scoring.

## Technical Design and Interfaces

### Application stack

- One FastAPI/Uvicorn Docker container.
- Static multi-page HTML, CSS, and modular JavaScript; no heavy frontend framework.
- Pydantic request/response validation.
- Psycopg 3 connection pooling and PostgreSQL transactions.
- Alembic database migrations protected by a PostgreSQL advisory lock during deployment.
- Pytest for backend/unit tests and Playwright for browser workflows.
- Same Hugging Face deployment script, GitHub Actions pattern, health checks, and watchdog approach as the ambulance application.

### Data model

Use UUID identifiers and a dedicated PostgreSQL schema with these groups:

- Journeys and configuration: `journeys`, `journey_activities`, `rubric_snapshots`.
- People: `recruits`, `recruit_photos`, `evaluator_directory`, `journey_evaluators`.
- Rooms: `rooms`, `room_recruits`, `room_evaluators`, `mandatory_room_evaluators`.
- Assignments: `assignment_rounds`, `assignments`, including preview/published/superseded versions and generation seed.
- Evaluations: `evaluation_submissions`, `evaluation_responses`, `submission_versions`.
- Results: database views for activity scores, averages, ranks, overall scores, and completion.
- Administration: `general_assessments`, `admin_sessions`, `evaluator_sessions`, `audit_events`.

Photos are resized to a maximum of approximately 512×512, converted to WebP, capped around 256 KB, and stored as PostgreSQL `BYTEA`. They are served only through authenticated, access-checked endpoints with private caching.

All mutable admin records carry a version number. A save based on stale data returns a conflict instead of silently overwriting another admin’s work.

### Main HTTP contracts

Admin contracts:

- Login/logout and session status.
- Journee create/list/update/duplicate/archive.
- Recruit/evaluator import, roster management, photo upload, and confirmed attendance saves.
- Room preview/publish and manual room changes.
- Assignment preview/publish and manual swaps.
- Activity open/close/reopen.
- Live monitoring, rankings, averages, recruit profiles, corrections, audit, and exports.

Evaluator contracts:

- Resolve the single Active Journee from the permanent evaluator endpoint.
- Start a name-bound evaluator session.
- Read current published tasks and assigned photos.
- Save drafts, submit, and edit an evaluation while open.
- Poll a lightweight version endpoint for assignment/activity changes.

Every mutating request uses CSRF protection, an idempotency key where double submission is possible, and a database transaction.

### Security and reliability

- Admin password stored as an Argon2 hash in `LRC_JOURNEE_ADMIN_PASSWORD_HASH`.
- Secure, HttpOnly, SameSite session cookies and login rate limiting.
- Evaluator sessions scoped to one Journee and one selected evaluator.
- The evaluator link and QR are permanent; the server allows at most one Active Journee and resolves it automatically.
- Public Space source contains no credentials, database URLs, photos, or operational data.
- Audit before/after values for sensitive changes.
- Database constraints prevent duplicate assignments, responses outside valid ranges, and more than two evaluators per recruit.
- Automatic updates use conditional polling every five seconds while active and back off while idle; Refresh bypasses the wait.
- Evaluator drafts survive reloads and temporary network loss. Final submission requires server acknowledgement.
- `/health/live` checks the process; `/health/ready` checks database connectivity and migrations.
- Reuse the existing external watchdog, with a five-minute event-day health check.
- Pre-warm/restart the Space before each Journee. CPU Basic can sleep after inactivity, so the event runbook must verify it is awake before evaluators arrive. [Hugging Face hardware and sleep behavior](https://huggingface.co/docs/hub/en/spaces-gpus)

## Test and Acceptance Plan

### Scoring tests

- Every rubric’s weights total exactly 1.00.
- Grades below 0 or above 5 are rejected.
- General grades accept only 0.0–1.0 in 0.1 increments.
- Sport accepts every documented duration format and rejects ambiguous/negative values.
- Results at or above each sport target cap at 5.
- `30, 120s, 600s, 120s` produces Sport `5.00`.
- A missing co-evaluator does not dilute a submitted score; an activity with no submission and missing general grades contribute zero.
- Overall maximum is exactly 20.
- Color boundaries at 13 and 16 are correct.
- Activity and overall ties produce `1, 2, 2, 4`.

### Assignment tests

- Balanced recruit rooms differ by no more than one.
- Mandatory evaluators remain in their configured rooms.
- Overall evaluators receive primary priority.
- Every recruit receives a primary before second assignments.
- Normal evaluator capacity is one.
- Shortage loads are balanced and no second evaluators are created.
- Surplus evaluators never create more than two evaluators per recruit.
- Pair repeats are avoided across independent activities when feasible.
- Forced repeats are minimal and explained.
- Negotiation rematches only within rooms; Skills rematches globally.
- Simulation exactly matches Skills.
- A fixed seed reproduces the same preview.
- Manual swaps cannot violate room, attendance, or maximum-evaluator constraints without an explicit override.

### Workflow, concurrency, and security tests

- Unsaved attendance/profile changes never become visible to another client.
- Assignment preview never changes evaluator-visible tasks before publication.
- Two admins editing the same version produce a conflict, not lost data.
- Duplicate submit taps create one submission.
- Evaluators can edit while open and cannot edit after closure.
- Reopening and admin corrections create audit versions.
- Evaluators cannot access other assignments, photos, comments, rankings, or admin APIs.
- Rotated or archived Journee links stop creating sessions.
- Photo endpoints do not expose permanent public URLs.
- Database restart or Space restart loses no confirmed data.

### Performance acceptance

Using 100 recruits, 100 evaluators, and up to 20 rooms:

- Room and assignment previews complete within five seconds on CPU Basic.
- Normal API writes respond within two seconds under event-day load.
- Monitoring changes appear automatically within ten seconds and immediately after Refresh.
- Evaluator pages remain usable on current Android/iOS browsers and slow mobile connections.

## Delivery and Migration

1. Build locally against a disposable PostgreSQL schema with seeded demonstration data.
2. Deploy a separate test Hugging Face Space and run the full Journee lifecycle.
3. Conduct user acceptance with LRC admins and evaluators using fictional recruits.
4. Pilot one Journee in parallel with the current Google Sheets system.
5. Verify database backup, export, restore, assignment generation, and event-day health procedures.
6. Deploy the production Space under the existing `lrc203` account and copy the required database and notification secrets.
7. After the system is accepted, receive the previous Journee exports and implement a one-time, validated migration into the same schema. Historical migration is intentionally deferred until those files are supplied.

## Locked Assumptions

- English-only first version.
- Designed and tested for up to 100 recruits.
- Shared admin password plus required display name.
- Evaluator name-only selection; impersonation cannot be fully prevented.
- PostgreSQL is authoritative; no live Google Sheets synchronization.
- Fixed 2026 rubric with no admin rubric editor.
- Confirmed-present recruits alone participate in assignments, averages, and rankings.
- Missing expected values count as zero.
- Shared competition ranking.
- Recruit photos are visible to admins and currently assigned evaluators only.
- All timestamps are stored in UTC and displayed in Asia/Beirut.
- Historical data migration occurs after the new system passes acceptance.
