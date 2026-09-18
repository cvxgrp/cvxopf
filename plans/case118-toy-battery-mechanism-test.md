# Case118 toy study: battery operation in AC and DC

Status: the 12 comparisons of fixed versus rescheduled battery power are
complete; independent implementation and numerical reviews are CLEAN.
See [Battery operation in AC and DC](../experiments/case118_counterfactual/battery_operation/REPORT.md).
The owner has reviewed the results and authorized step 3, the 1 and 5 MWh
transfer comparisons. Implementation passed independent review, the owner
committed it as `dd21216`, and the authorized 12 comparisons have now run.
See the [result report](../experiments/case118_counterfactual/battery_operation/TRANSFER_REPORT.md):
an additional owner-requested June 16 5 MWh AC solve returned `optimal` with
lower cost. Both attempts are retained. Results await owner review.
The protocol below records the agreed design.

## Question and limits

Does the AC model offer an economic incentive for an endpoint-preserving
battery energy shift that is absent or weaker in the matched DC model?
Retain the original toy economics: generator curvature 1e-4 and battery
throughput weight 1.0. This tests a possible explanation for the historical
activity despite those economics; it does not test the intended Tracy
levelization mechanism. The separate toy horizon study remains deferred.

The completed AC dispatch comparison at [2940,2943) found a substantial
generator redispatch improvement and no resolved incremental battery benefit. It began
with a common DC SoC, while historical AC began about 63 MWh below that state.
The new windows instead start where historical AC is already close to DC and
plans an appreciable charge/discharge excursion with nearly unchanged endpoints.

The read-only screen verified 181 original accepted controlling plans. Of
these, 142 have initial fleet L1 AC–DC SoC gap <=0.001 MWh, first-hour battery
L1 departure >=10 MW, three-hour horizons without a reset/crossing or recorded
operator intervention, endpoint movement <=0.001 MWh, DC throughput <=0.001 MWh,
and AC throughput exceeding the endpoint-implied minimum by >=20 MWh.
These are windows, not independent episodes. Full planned trajectories were
checked, not concatenated implemented actions from overlapping solves.

Screen provenance and arrays are retained in
`experiments/case118_counterfactual/battery_operation/screening/`; interval-table SHA256 is
`9297a45485a8bee3caf035f1efc0dc1966f8ffd561e0863528ad39915b05b76e`.

## Owner-selected windows

Times are in the synthetic calendar, UTC. Each window has three one-hour steps.
These are the three examples presented to and selected by the owner, not the
top three of an annual ranking or a representative/random sample.

| Start | Indices, half open | Historical planned fleet power, MW (+ discharge) |
| --- | --- | --- |
| May 3, 2025, 16:00 | [2944,2947) | -186.424, -0.327, +186.750 |
| May 22, 2025, 16:00 | [3400,3403) | -207.480, -0.385, +207.866 |
| June 16, 2025, 16:00 | [4000,4003) | -197.525, -0.317, +197.842 |

Original controlling archive SHA256, respectively:

```text
d0a6eac9b178f7df850fde8f68959dcee91e584c43d1c0f5973405f5013e5cf2
4cfcbddb3c17676a22680751bea8a8be7acdce39592cf0bfa2e9785d25c967bd
1b0404fd5e889ef452bb41d0001a7d88dd51500c992a09a4880dba84d13cd190
```

Retain 48-hour context for each window, marking source shard boundaries.
May 3 already has context [2916,2964) from the AC dispatch comparison. Extract
[3376,3424) and [3976,4024) for the other two. Context does not change the
three-hour solve horizon. Freeze full source references, identities, transfer
weights, arrays and numerical settings in a machine-readable launch manifest.

All three contexts are now extracted. The inspected figure
`experiments/case118_counterfactual/battery_operation/screening/selected-context.png` overlays the
original planned trajectories on the implemented paths. Later reoptimization
changes the actual discharge timing; the historical three-hour plan must not
be described as three implemented actions or a full realized cycle.

## Common conditions and objective accounting

Use the authoritative S4 outer archive and unchanged toy fixture. All arms
start at DC SoC[t] and end at DC SoC[t+3], preserving per-device identity.
Use identical loads, generator costs/limits, storage capacities/ratings and
renewable real output fixed to DC. Keep AC reactive controls free within their
original bounds and apparent-power circles. Do not import AC reactive physics
into DC or silently relax an infeasible restriction.

AC retains its original generation-plus-storage objective. DC retains its
original generation-plus-storage-plus-weighted-loss-proxy objective and
historical loss weight. Report generation, storage, common device-cost sum,
DC loss-proxy contribution, and native objective separately for every arm.
Measure benefits within each formulation; do not subtract raw AC/DC objective
levels as a price or welfare comparison. A difference between models does not
by itself isolate congestion, reactive restrictions, or physical losses, and
the DC objective's loss proxy is another explicit modeling difference.

Initial historical AC–DC gaps are tiny but nonzero. Historical AC solutions
are descriptive evidence, not automatically feasible matched-arm incumbents.

## Step 2: fixed versus free battery power

For each window and each formulation:

1. **F:** fix battery real power to the DC schedule and optimize generation
   and other available controls under the native objective.
2. **B:** free battery real power, retaining the same endpoints, and optimize
   the same objective. Retain and re-audit F as a feasible incumbent.

This requires 2 AC + 2 DC primary solves per window, 12 across three windows.
R1/R2 repair objectives and departure budgets are not part of this test.
Compare F–B cost changes and components, power/SoC trajectories, throughput,
network losses/margins, and first actions against the historical planned
excursions. Reoptimization does not replay the complete historical controller.

The DC comparison also checks reconstruction consistency. If the short model
faithfully reproduces the annual convex problem restricted to these inputs
and endpoints, its accepted annual schedule should already offer no resolved
native-objective improvement from local battery rescheduling. A material DC
F–B improvement should first trigger numerical/model-match scrutiny, rather
than immediately be interpreted as a newly discovered economic opportunity.

Checkpoint status: the owner reviewed all three step-2 comparisons and
authorized step 3.
An unresolved or null comparison is an outcome, not grounds to select another
window, relax physical checks, or expand the horizon automatically.

## Step 3: two prescribed energy transfers

Propose E = 1 and 5 MWh of fleet energy moved from hour 1 to hour 3.
These are deliberately small relative to the roughly 186–208 MWh historical
first-hour charging actions. They are two finite perturbations, not a
certified infinitesimal derivative or an exhaustive sensitivity curve.

For each battery s, define from its retained historical plan

```text
q_s = max(0, min(-b_historical[0,s], b_historical[2,s])) * delta
w_s = q_s / sum_s q_s
```

Use the same frozen weights in AC and DC and for both magnitudes. This selects
the observed first-hour-charge/third-hour-discharge direction (buses 41 and 65
in these three windows) without fitting weights to new cost outcomes.

| Start index | Bus 41 weight | Bus 65 weight | Other batteries |
| --- | ---: | ---: | ---: |
| 2944 | 0.0755158514 | 0.9244841486 | 0 |
| 3400 | 0.0675089409 | 0.9324910591 | 0 |
| 4000 | 0.0713164756 | 0.9286835244 | 0 |

Both proposed magnitudes passed read-only storage power/SoC/endpoint checks
in all three windows. Exact weights, schedules, states and residuals are in
`experiments/case118_counterfactual/battery_operation/screening/proposed-transfer-prechecks.json`.
Use the full-precision values in the launch manifest, not this rounded table.

Prescribe the full battery schedule as DC plus

```text
Delta b[0,s] = -E*w_s/delta
Delta b[1,s] = 0
Delta b[2,s] = +E*w_s/delta
```

The transfer leaves each battery's terminal SoC unchanged. Check the complete
prescribed SoC path and real-power bounds before any solve. Passing those
checks does not guarantee AC network/reactive feasibility. A failed check or
unresolved solve is reported; do not silently shrink E or redistribute weights.

For each E and formulation, fix this schedule and reoptimize generation and
other controls. Reuse that formulation's F cost from step 2 as the baseline.
There are 2 AC + 2 DC additional primary solves per window, 12 total.
F is an initialization source, not a feasible incumbent under the changed
schedule. Preserve each perturbed arm's constraints in every helper attempt.

Report native-objective and device-cost changes divided by E, component
changes, and exact throughput increments. From an idle lossless battery,
the additional throughput penalty would be 2*E at the toy weight of 1;
use actual reconstructed throughput rather than assume exact idleness.
Compare sign and approximate cost change per MWh across the two magnitudes.
Do not label these finite schedule sensitivities as nodal dual prices.
No multiplier-extraction implementation is required for this bounded test.

## Counts, resources, and initialization proposal

| Phase | AC primary solves | DC primary solves | Total |
| --- | ---: | ---: | ---: |
| Step 2, three windows | 6 | 6 | 12 |
| Step 3, three windows | 6 | 6 | 12 |
| Full proposed test | 12 | 12 | 24 |

Reuse two AC main lanes and one shared helper, parallelizing windows with
dependent arms ordered. Keep the existing 16 GiB worker / 24 GiB aggregate
RSS limits, 8 GiB helper reservation and 22 GiB pressure threshold. The earlier
12 GiB analysis-job limit is unrelated. Run DC jobs serially before the AC
batch in each phase, so DC does not create a fourth concurrent solver worker.

AC F uses the existing flat primary start; B uses the accepted F solution;
prescribed-transfer arms use the corresponding F solution as initialization.
Keep complete physical/canonical start retention and source identities.
Fixed-battery F/transfer arms reuse explicit primary/source-start perturbations;
target-free and dependent starts are inapplicable. Free B retains the existing
target-free recovery ladder, with only endpoint-conditioned results eligible
as candidates. This does not add solver methods or a scheduler architecture.

Proposed hard budgets across both phases: 48 AC attempts including primaries,
helpers and replays, plus exactly the 12 planned DC attempts (no automatic DC
retry); hence at most 60 launched optimization attempts. Limit active study
wall time to 3 hours and summed worker elapsed time to 6 hours, with 90 minutes
maximum per worker including construction. The owner-review pause between
phases is not active wall time. Carry cumulative consumed budgets into step 3;
do not reset them or transfer unused counts into unplanned configurations.
Preserve the existing helper clocks/escalation and interrupt remaining workers
at a total limit, retaining partial evidence. Count failed/canceled work.

Keep frozen physical acceptance tolerances and prior AC comparison checks:
schedule locks 1e-4 MW, cost reconstruction 1e-4 absolute + 1e-9 relative.
Use original frozen AC solver options and current verified DC solver defaults,
recording effective options and versions before launch. Add independent DC
balance/branch/storage checks and native loss-proxy reconstruction. Numerical
acceptance tolerances are not optimality-error bounds or significance tests.

## Interpretation and execution sequence

A feasible cost-reducing AC transfer demonstrates an available improvement
over its retained fixed-schedule baseline. A weaker/absent matched DC incentive,
together with agreement with the historical direction, supports the proposed
model-dependent economic mechanism in these episodes. Local AC solves cannot
certify the optimal restricted baseline, global battery value, or a unique
historical cause. Network necessity and individual constraint attribution are
not resolved by observing battery activity alone. Retain null/unresolved
results and do not generalize this selected sample to an annual frequency.

1. Review this design and proposed parameters/resources with cvxopf-review and
   the owner; inspect the retained context. No solves at this checkpoint.
2. Implement narrow reusable F/B/prescribed-schedule support and DC comparison
   accounting; preserve the existing AC comparison code. Verify identities,
   units, endpoints, objective components and both formulations' audits.
3. Independently review and commit implementation before numerical execution.
4. Freeze launch manifests and execute step 2 only after launch authorization.
   Review results before deciding to execute the already scoped step 3.
5. Record results, unresolved questions and any explicitly deferred work at
   the Stage 0c closeout gate. The user still handles merge/main/Tracy branch.

Phase-one entry point: `experiments.case118_counterfactual.mechanism`. It reuses
the existing AC G/B models (G is F here), runs DC serially first, and records
cumulative budget consumption. The current implementation ends after step 2;
prescribed-transfer execution uses `experiments.case118_counterfactual.transfers`
after implementation review and the execution-source checkpoint.
The completed comparisons used this launch manifest:
`experiments/case118_counterfactual/battery_operation/protocol.json`.

Step 3 launch protocol:
`experiments/case118_counterfactual/battery_operation/transfer-protocol.json`.
It binds the preceding study/summary and pre-outcome historical schedules and
transfer prechecks. Remaining budgets are derived from the preceding recorded
consumption; the owner-review pause is excluded from active time.

An optional `--snapshot-reviewed-worktree` execution route has been proposed
to preserve the owner's commit control while retaining immutable source bytes
and hashes. This is a proposed alternative to the commit checkpoint, not an
automatic waiver. Obtain the owner's explicit disposition before using it;
otherwise use the reviewed, committed implementation as specified above.
