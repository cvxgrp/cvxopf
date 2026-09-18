# S5 machine observations

## September 14, ~4:30 PM PDT: external fan added (operator-reported, approximate)

Reported by the operator on September 15, 2026: an external fan was directed
at the underside of the laptop because it was very hot to the touch. It was
not hot to the touch the following morning.

The approximate local timestamp is `2026-09-14T16:30:00-07:00` (23:30 UTC).
This is a qualitative operator observation, not an instrumented temperature
measurement. No measured thermal-throttling change or causal speedup is asserted.

Retain this event alongside the September 14 policy launch (14:32 PDT) and
computer reboot (15:43 PDT) in future timing plots. Improved cooling is another
possible contributor to the observed post-reboot performance, so that period
should not be treated as isolating reboot alone.

The annotated 07:54 PDT September 15 completion snapshot is in
`s5_completion_walltime_update_20260915145440/completion_walltime_with_fan.png`;
its machine-readable event notes are in the same directory's `operator_markers.json`.
These local analysis notes do not change the running experiment or its authority.

## September 15, ~9:10 AM PDT: VPN and Teams activation (operator-reported)

At `2026-09-15T09:10:13-07:00` (`2026-09-15T16:10:13Z`), the operator
announced that they were turning on their VPN and Microsoft Teams app.
This timestamp records the announcement; actual activation times were not
independently observed and are approximate. The operator expressed concern
about possible slowdown. No performance effect has yet been established.

Retain this event in subsequent timing plots for comparison. No study process,
policy, execution source, or authority was changed to record it.

## September 15, ~9:15 AM PDT: Outlook activation (operator-reported)

At `2026-09-15T09:15:42-07:00` (`2026-09-15T16:15:42Z`), the operator
added that Microsoft Outlook was also being opened. This records the
announcement, not an independently measured application-launch time.
Retain separately from the ~9:10 AM VPN/Teams marker; no performance effect
has yet been established.

## September 15, 12:00 PM PDT: Teams call scheduled (not yet observed)

The operator reports a Teams call planned for `2026-09-15T12:00:00-07:00`
(`2026-09-15T19:00:00Z`). This is a scheduled event, not confirmation that the
call occurred or its actual duration. Actual start/end and any observed
performance effect remain to be recorded. A live call is distinct from
simply having Teams open since approximately 9:10 AM.

### Follow-up: meeting completed with screen sharing

The operator confirmed completion of the Teams meeting with screen sharing
at approximately 13:08 PDT on September 15. The comparison used the planned
12:00 start and approximate 13:08 end; the actual start was not independently
measured. No overall slowdown was apparent in that comparison.

## September 15, ~1:12 PM PDT: external fan increased from level 1 to 2

At approximately `2026-09-15T13:12:20-07:00` (`2026-09-15T20:12:20Z`),
the operator reported increasing the external fan from level 1 to level 2
on its five-level control. The timestamp records the report, not an
independently measured switch time. Immediately beforehand, the operator
reported higher CPU temperature visible in `macmon`; no numerical temperature
was supplied or captured here. No throttling or performance effect from this
fan adjustment is asserted. The study processes and policy remain unchanged.
