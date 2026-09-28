# Management corrections: design for approval (not implemented)

The user approved uniform target grades on 2026-09-28: an activity changed
from average 3/5 to 4/5 with original criteria 2/5 and 4/5 becomes effectively
4/5 and 4/5. The full persistence/UI/migration design below still needs review.

## Purpose and current boundary

Management needs a simple correction workflow without rewriting evaluator
submissions. The rank/color-column release does not implement corrections.
Existing General Assessment editing remains available and is not replaced.

Current scoring has two paths: activities average submitted scores (or apply
the configured missing-as-zero rule); dimensions either reference an activity
or aggregate normalized criterion grades. Existing AdminEvaluation records
take precedence over evaluator aggregates. Preserve these automatic rules and
all their input records when adding a management correction layer.

## Proposed representation

- Keep original submissions, version histories, measured Sport counts/times,
  and existing AdminEvaluation records unchanged.
- Add a workspace/Journee/recruit-scoped, revisioned correction record holding
  effective normalized criterion values and an optional configured color-band
  key. Bulk activity/dimension corrections write this same criterion map;
  they are not independent contradictory hard overrides at several levels.
- Store one atomic audit event per operation using existing AuditEvent:
  actor, time, target level/key, requested target, before/after effective
  values, affected criteria, configuration revision, and reason.
- Optimistic revision checks, existing permission/CSRF checks, one transaction
  for the criterion map and audit. No partial saves. Proposed authorization:
  the same existing management-results permission used for General Assessment
  edits (plus administrators). No new account permissions are granted.
- UI distinguishes original evaluator average, automatic result (including
  existing admin evaluations), and effective corrected result. History remains
  available when restoring automatic values.

## Propagation math: uniform target approved by user

Normalize the requested aggregate grade to 0..1 using its configured display
maximum. Write that same normalized grade to every positively weighted
criterion contributing to the target activity or dimension. Zero-weight
criteria are not contributing and stay unchanged. For activity-backed
dimensions, expand to that activity's contributing criteria. Reject a target
with no positively weighted criteria and explain the configuration problem.

Example: two equally weighted criterion grades 2/5 and 4/5 average 3/5.
Setting the activity to 4/5 produces effective grades 4/5 and 4/5. Setting it
to 5/5 produces 5/5 and 5/5. Original grades remain 2 and 4. This is not a
proportional/headroom adjustment and not a uniform addition of points.

For configurable criterion input scales, map the normalized correction back
to minimum + t * (maximum - minimum). Target-based criteria remain converted
0..5 grades; measurements stay unchanged. Direct criterion editing uses its
displayed grade scale with explicit validation.

After any edit, recalculate affected activities, every dependent dimension,
overall score, completed-workspace/Journee ranks, and automatic color. Later
overlapping edits update shared criteria; show the resulting changes before
saving. Do not keep an earlier aggregate target artificially fixed.

Sport adjustments never fabricate faster run times or more repetitions. Use
Decimal internally, retain precision for adjusted criterion averages, and
round at output. Proposed distinction: the evaluator input step constrains
evaluator input, not a computed management-adjusted average. Without any
corrections, the existing scoring path and rounding must remain exactly intact.

## Proposed edge cases for approval with this design

- Bulk adjustment explicitly covers all contributing criteria, including any
  ungraded criteria. Preview names those missing grades before saving. Never
  create evaluator submissions or change the submitted/expected counters.
  Show 'Manually graded' coverage separately from evaluator completion.
- A later bulk correction replaces any previous effective corrections for
  the affected criteria only. Preview replaced adjustments; one audit event
  contains every changed value. Other criteria remain untouched.
- Configuration changes cannot silently reinterpret a correction: retain its
  configuration revision and mapping in history, validate against current
  keys/scales, and show a conflict requiring review if they no longer match.
- The raw, automatic and effective values are distinct. Existing explicit
  admin evaluations remain part of the automatic baseline; they are not
  deleted or rewritten by a management correction.

## Restore and manual color

Proposed restore removes selected criterion overrides with a new audit event,
then recalculates from current automatic inputs; it never deletes audit history.
An aggregate restore previews exactly which criterion corrections it removes.
Undo restores a prior map only with a matching current revision.

Manual color uses the configured band keys and changes effective color only.
Automatic color remains visible. Restore automatic color clears the color
selection with a new audit event; scores and ranks never change due to color.
The color correction can reuse the same revisioned persistence/audit model.

## UI and safe rollout

An Edit grade action on criterion/activity/dimension opens a small form with
the current automatic and effective grade, target input, optional reason, and
a before/after preview of affected criteria and totals. Save is explicit for
bulk corrections. General Assessment keeps its current autosave workflow and
existing history. Color has a configured-band dropdown, Manual marker, and
Restore automatic color action. No new background backup/autosave proposal.

Persistence requires an additive migration with no backfill of submissions or
scores. Before rollout: backup/readiness verification, migration rehearsal on
disposable restored data, exact no-correction parity, forward migration once,
then deploy Oregon and main sequentially. Old code can coexist with the new
empty table before activation, but after corrections are entered it would
ignore them: rollback must first disable editing and preserve/export that
layer, and must not be presented as mathematically equivalent to the new code.
No provider, region, hostname, plan, or other infrastructure changes.

## Required verification before deployment

Exact no-override parity (including existing rounding); configured scales,
weights, missing policies, target-based criteria, overlap, ties; audit/restore;
restart persistence; tenant isolation; authorization/CSRF; revision conflicts;
atomic failure; immutable submissions; score-neutral color selection; exports
and profile/table agreement; forward-only safe migration and rollback review.
