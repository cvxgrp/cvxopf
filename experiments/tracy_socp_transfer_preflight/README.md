# Tracy transfer preflight policy

The default report-only preflight checks three- and four-bus loops. Five- and
six-bus subdivision is an explicit, opt-in escalation for difficult segments,
not a routine scan or an automatic fallback after a failed AC solve.

## Default checks

Use the [multigraph checker](multigraph_loops.py), which already defaults to
`max_length=4` and `max_boxes=64`. It includes triangles and four-bus cycles,
with separate parallel-branch restrictions and fixed transformer taps and
phase shifts. Keep the existing stand-alone parallel-corridor checks as well.
In the retained Case118 topology, this covers 23 triangles, 28 four-bus loops
and seven parallel corridors.

Every supported loop receives the cached geometry-only root check. A positive
necessary aggregate-export bound triggers dispatch-dependent subdivision,
capped at 64 visited boxes per loop-hour, including its dispatch root. The
cached geometry root is separate from this cap. Keep this trigger and budget
unchanged; do not force subdivision by default or silently expand to longer
cycles.

The existing command for reproducing the default retained-data back-test is:

```bash
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --extra dev python \
  -m experiments.tracy_socp_transfer_preflight.multigraph --backtest
```

This is an explicit retrospective analysis command, not a production OPF
acceptance gate. No automatic production integration is introduced here.

## Opt in escalation

Consider five- or six-bus subdivision only when all of the following hold:

1. Evidence identifies a difficult segment or hour, such as repeated recovery
   difficulty, localized physical-audit residuals, or a retained-point cycle
   mismatch. Solver rejection alone is not an infeasibility certificate.
2. The default shorter-loop checks have not produced a certificate explaining
   the issue. If they have, retain that certificate rather than adding a broad
   longer-loop sweep merely to rediscover an exclusion.
3. Selected longer loops traverse the implicated buses or branches, and their
   examination has a stated diagnostic purpose.

Before running an escalation, explicitly record the evidence motivating it,
the affected hours and network neighborhood, the selected cycles, and the
64-box cap per selected loop-hour. Prefer this focused scope to checking every
longer loop throughout an entire retained study. Bypassing the positive-export
trigger is permitted only within that explicitly requested escalation; it
does not justify inventing positive bounds or increasing the cap.

Use the [segment selector](segment.py) for focused escalation. It requires
one retained transfer job, exact global hours, a bus/branch focus and an
escalation reason. It previews the selected scope by default; `--run` explicitly
enables the selected interval checks. The per-loop/hour cap stays at 64.

The existing `long_loops` and `forced_subdivision` drivers remain full-cohort
research/reproduction tools, not focused segment checks. Do not invoke those
batch drivers automatically from the default preflight or the segment selector.

## Select a segment

`--hours` selects exact global input-hour indexes, deduplicated and sorted.
`--buses` uses external MATPOWER bus IDs. Alternatively, `--branch-rows` uses
zero-based row positions in the original MATPOWER branch table; an active
selected branch seeds both endpoints. Bus and branch seeds can be combined.
Unknown buses, inactive/out-of-range rows, and hours outside the retained
horizon are errors, not reasons to widen or clip the scope.

`--hops` expands the focus along active undirected corridors, default zero.
Parallel branches and transformers each count as one corridor hop. Select
complete five-/six-bus loops touching at least one neighborhood bus; the rest
of a selected loop may extend outside that neighborhood. A branch seed does
not require every selected loop to traverse that exact branch. The full network
and original time-series offsets remain intact for all necessary cut bounds.

For example, preview a scope near the historical bus-71 mismatch:

```bash
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --extra dev python \
  -m experiments.tracy_socp_transfer_preflight.segment \
  --job 13 --hours 2452 2453 --buses 71 --hops 1 \
  --reason "Preview topology near the retained bus mismatch"
```

This example is a selector demonstration, not a recommendation to repeat the
hour-2452 investigation: a shorter-loop certificate already explains it.
Preview replays one retained transfer and reports the selected cycles, hour
offsets, omitted hours/loops and compute cap without running proof searches.
After verifying that a genuinely difficult unexplained segment warrants
escalation, append `--run` to execute only the selected checks. Use
`--schedule ac-control` to inspect the accepted AC schedule for the same
scenario, keeping its identity separate from the donor's historical outcome.

The output is a JSON report on stdout; it does not overwrite saved results.
An empty neighborhood selection reports `no_loops_selected` when execution is
requested, not feasibility. Unselected hours and loops remain unexamined.
See [the segment protocol](SEGMENT_PROTOCOL.md) for API and preservation rules.

## Evidence behind the policy

The [forced longer-loop back-test](FORCED_SUBDIVISION_REPORT.md) checked all
27 five-bus and 30 six-bus loops across 432 donor hours and 144 AC-control
hours. All 32,832 loop-hour searches exhausted 64 boxes without excluding
even one box or finding a new certificate. There were no contradictions with
accepted witnesses. These results support escalation-only use with the
current bounds, not a claim that longer loops can never be useful.

The [default multigraph back-test](MULTIGRAPH_REPORT.md) retains the four-bus
certificates at SOCP hours 2452 and 2453, lossy DC hour 2452, and copper-plate
hour 2452. Triangles remain in the default coverage even though the retained
triangle checks yielded no additional certificates. Future detection value
is not established by this retrospective sample.

## Interpretation and preservation

Only a complete exclusion of the root voltage domain is a certificate of
incompatibility under the checked physical assumptions. No export trigger,
unsupported geometry, or an exhausted box budget remains unresolved, never
certified feasible. Retained SOCP point warnings and independently audited AC
witnesses remain distinct from schedule-incompatibility certificates.

Neither default checks nor escalation may change physical constraints,
battery power pins, initial SoC or dynamics, endpoint audits, tolerances, or
historical classifications. All diagnostic work remains report-only and
non-solving. Preserve unsuccessful and censored outcomes, all existing source
and evidence hashes, and source E3/E3+ and historical Stage D qualifications.

This README is the current operating policy. Historical checkpoint protocols
and reports remain unchanged:

- [Original point screens and named-loop protocol](PROTOCOL.md)
- [Automatic loop discovery protocol](AUTOMATIC_PROTOCOL.md)
- [Parallel branches and transformer protocol](MULTIGRAPH_PROTOCOL.md)
- [Triggered five-/six-bus back-test](LONG_LOOPS_REPORT.md)
- [Forced subdivision checkpoint protocol](FORCED_SUBDIVISION_PROTOCOL.md)
