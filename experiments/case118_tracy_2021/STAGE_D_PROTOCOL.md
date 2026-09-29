# Stage D — bounded Tracy AC qualification

Status: **opened by owner authorization after shard checkpoint `e09af9dc7`**.
The owner approved the initial design below. Exact start times, solver/start/
recovery settings and resource budgets must be reviewed and frozen before
execution. No Tracy AC solve has run. Annual AC execution remains separately gated.

## Fixed scientific configuration

Keep the approved Stage A fleet, input series, Stage C economics (rho = 1/3,
lambda = 0.01), shedding policy and ratings. Use the accepted annual lossy-DC
archive, not a new outer solve or copper-plate targets. Archive SHA-256:
`f984b690497d60dd983314f168be641063a933b0b1b0c717a94f0341d76e9991`.
Verify the [shard manifest](SHARD_BOUNDARIES.json) and device identities.

Retain reactive load signs/Q:P ratios, source generator Q limits, renewable
MVA ratings at 110% of annual peak availability, and battery apparent-power
circles. Enforce voltage and both-terminal branch MVA limits. No extra reactive
regularization, model tuning, or resource resizing.

Use vectorized AC with vectorized sparse P/Q and **automatic sparse dispatch
disabled**, through DNLP/IPOPT. Reuse existing initialization conversion,
complete-x0 capture, acceptance and recovery logic. Do not import historical
numerical starts or rebuild the scheduler.

## D1 — four-period, six-hour rolling horizon comparison

This **replaces**, rather than supplements, the earlier proposed four detached
three-hour probes, 16-request matched-state screen, and two 12-hour rollouts.
No separate numerical warm-up batch is implied.

| Dimension | Approved design |
|---|---|
| Operating periods | Large surplus; large deficit; surplus → deficit; deficit → surplus |
| Implemented duration | Six consecutive hourly actions from each start |
| Look-ahead W | 1, 3, 6, 12 hourly steps; W=3 is the reference |
| Resolution / stride | `delta=1` hour; execute only the first hour of each solve |
| Controlling decisions | 24 per W; **96 overall**, before recovery attempts |
| Review stop | Evaluate these four periods before selecting a larger qualification set |

This gives 16 separate six-hour controller trajectories. Ninety-six controlling
solves are planned if all trajectories complete; failures, diagnostics, recovery
and repeats add attempts and must be separately bounded. Never fill missing
actions in an incomplete rollout from DC or another controller.

### Select and freeze four start times

Propose starts from prepared inputs and accepted DC results before observing
AC outcomes. Available net load is gross load minus all available renewables,
before curtailment or storage. Surplus → deficit is an **increase** in net load;
deficit → surplus is a **decrease**. Use these explicit names instead of
ambiguous “ramp up/down.”

The six implemented hours must capture the intended regime or transition,
not merely an extreme somewhere in the look-ahead. Inspect input and DC SoC
context and freeze the exact selection rules, starts, tie-breaking and overlaps
for owner review. Do not automatically reuse earlier three-hour extrema.
These deliberately selected regimes are not an annual random sample.

Require `[t0,t0+17)` to fit inside the year and one shard to avoid confounding
horizon choice with boundary truncation. The six solves begin at t0 through
t0+5; the final W=12 solve ends at boundary t0+17. All arms implement only
`[t0,t0+6)`; the eleven additional intervals are look-ahead, not executed work.

### State, targets and initialization

At each period's first hour, all W start from the **same 27-device annual
lossy-DC SoC vector at t0**, not the input helper's annual 50% defaults.
Thereafter, each controller advances its **own accepted realized state** and
retains its own preceding accepted AC solution. Do not reset to DC each hour
or share realized states/starts between arms.

At global solve start t, target the exact annual DC per-device SoC at t+W
with a hard equality. The outer trajectory stays frozen. Execute only the
first hour. Do not add a common terminal target at the six-hour reporting stop.

Both look-ahead information and the relevant terminal signpost change with W.
This tests the controller design, not horizon length isolated from terminal
policy. **W=1 ideal-storage hard endpoints fix first-hour battery active power**;
reactive support and other dispatch decisions remain optimized.

Keep solver settings and causal initialization/recovery policy fixed across W.
Freeze exact IPOPT options, first-window start, shifted/padded transformations,
perturbation scales/seeds and any permitted recovery sequence before execution.
There is no preceding AC state for the first decision. Test horizon-dependent
start handling with cheap construction checks and retain complete IPOPT x0,
including auxiliary variables; different-dimensional canonical vectors are
not claimed identical.

Balance rollout order across W and record host/cooling context. Do not cross
this initial experiment with stepwise assembly or alternative helper policies.
No numerical repeats without a declared budget.

### Measurements

- Aligned implemented battery P/Q and SoC, generator P/Q, renewable dispatch/
  curtailment, served/shed load, voltages and branch loading, by fleet and device.
  Include deviations from annual lossy-DC guidance.
- Six-hour executed cost components, energy, throughput, shedding and final
  stored energy. Unequal final SoC can explain apparent cost savings.
- Compare first actions at the common initial state separately from later
  closed-loop actions, whose states and causal starts have diverged.
- Retain full planned windows as diagnostics; do not compare raw objectives
  over unequal look-ahead lengths as equal amounts of executed operation.
- Construction, canonicalization/solve, native solver, extraction/audit,
  archiving, total action latency, iterations and sampled RSS. Include failures
  and recovery in effort totals.
- Completion/recovery counts, rejected attempts, timeouts and cancellations.
  Do not report only successful-solve averages or omit expensive horizons.

### Required checkpoint and restart

The owner requires this initial study to be restartable partway through. Treat
each `(period, W)` as an independent six-hour trajectory with a durable cursor;
also retain the study-level schedule and completed-trajectory registry. Reuse
the existing experiment checkpoint/archive conventions, without adding a new
general scheduling framework.

- Archive the accepted controlling result, physical audit, implemented first
  action, next realized device-aligned SoC, and the accepted solution needed
  for the next shifted initialization **before** advancing the cursor. Use
  atomic writes and reconcile a durable accepted result whose cursor update
  was interrupted, rather than solving or advancing that hour twice.
- On resume, verify the study specification, source/outer/signpost identities,
  period/horizon, numerical settings, execution source and retained accepted
  prefix. Resume from that trajectory's realized state and preceding accepted
  AC solution—not from nominal DC SoC, another W, or a new flat start.
  Skip already accepted actions and completed trajectories. Never overwrite
  retained results or silently reset accumulated work.
- Support an orderly stop that prevents new attempts, terminates/reaps active
  children as needed, and preserves available attempt records. Also handle an
  unexpected interruption by reconciling retained artifacts at restart. Never
  advance from an incomplete or unaudited solve. Ordinary restart is between
  solver attempts, **not** continuation of IPOPT's internal iteration state.
- An interrupted, unaccepted attempt may need to be retried under a new attempt
  identity. Retain its interruption and available timing/resource evidence,
  using the same pending control request and permitted causal sources. Keep
  any accepted diagnostic source required by the frozen recovery sequence;
  resume recovery at a defined position rather than silently changing policy.
- Budgets are cumulative across invocations. Resuming does not replenish
  attempt or worker-time allowances; distinguish active computation from
  operator downtime in reporting. If an abrupt crash leaves timing incomplete,
  retain that uncertainty and use a declared conservative accounting rule
  rather than assigning zero cost.
- Resume normally requires the same reviewed execution source and numerical
  environment. If code or scientific settings change, stop for an explicitly
  reviewed continuation decision; do not silently combine incompatible runs.
  Provide a concise human-readable stop/status/resume entry point and report
  exactly which trajectory and hour will run next before launching a worker.

Before numerical execution, exercise stop/restart and archive/cursor recovery
with synthetic subprocesses and analytically assigned fixtures. Cover a stop
mid-attempt, a stop after acceptance but before cursor advancement, resumption
mid-trajectory, and skipping a completed trajectory. Verify identical next-state
and shifted-start inputs, exactly-once advancement, and cumulative accounting.
No extra heavy AC solve is required merely to test restart mechanics.

### Budget, execution checkpoint and mandatory pause

The earlier four-probe/two-hour budget proposal is superseded. Neither the
annual DC budget nor an old three-hour AC limit automatically applies.
Approve per-attempt wall/RSS limits, recovery-attempt ceilings, cumulative
worker time, total elapsed budget, concurrency and stopping rules before launch.
Ninety-six controls do not bound all solver attempts. Retain failures without
silent retries, softened targets, changed physics or enlarged budgets.
An unsuccessful local solve is not proof of physical infeasibility.

Implement and test the bounded runner, request/state lineage, acceptance,
recovery and interruption paths before the reviewed execution commit. Avoid
heavy numerical tests merely to validate software seams.

After these four periods (or an earlier stop under frozen rules), report the
runtime/implemented-operation tradeoff and incomplete cases. **Pause for the
owner to decide which larger set of starts and comparisons to test.** Do not
automatically run the earlier eight-request set, reserved windows or 72-hour
rollout. No operational horizon is changed automatically.

## Later Stage D work — selected after D1 review

The parent plan's broader goals remain, but are not a frozen launch schedule:

1. Extend physical coverage to ordinary operation, gross load/congestion,
   sustained depletion/recharge and actual shard starts as warranted. Choose
   separate validation windows before further tuning. Six hours cannot
   establish performance through a multi-day deficit.
2. Compare time assembly and initialization on identical retained requests:
   vectorized primary, stepwise with the same named physical start, and a
   vectorized alternative from the existing ladder. Freeze the horizon and
   attempt table; count prerequisite source solves. Use a small declared
   repeat set rather than a full Cartesian grid.
3. If supported, exercise at most one challenger to the existing race baseline
   with two primaries and one shared helper. Freeze concurrent budgets and
   test contention, cleanup, acceptance and exactly-once advancement.
4. Qualify longer causal operation and a representative shard join/restart.
   Distinguish continuous realized-state handoff from independent starts at
   nominal DC states. Choose contexts and budget after the initial evidence;
   detached outcomes cannot be stitched into a rollout.

Earlier proposed starts, including December 19 and interior shard boundaries,
are possible future coverage, not approved numerical work. Preserve the owner's
interest in whether DC agreement during stress and differences during surplus
persist under AC realization.

## Evidence, outputs and final gate

Retain every request/attempt, start and x0, solver log/status/residual,
implemented action, phase timing, resource sample and provenance. Independently
audit AC P/Q balance, voltage, generator P/Q, both-terminal branch MVA,
renewable/storage capability, SoC recurrence/endpoints, load service and cost
accounting. Report DC-planned and AC-realized shedding separately. An accepted
target-free diagnostic is not a controlling hard-target action.

Deliver a report and read-only notebook with period/look-ahead selectors,
aligned implemented trajectories and differences, planned-window diagnostics,
timing, memory, residuals and incomplete outcomes.

Stage D exits with independently reviewed physical/runner-policy evidence,
a credible annual runtime/resource range and unresolved risks. Annual AC launch
requires a separate owner decision. No new DC solve, source rescaling, battery
resizing or shard-boundary revision is implied.
