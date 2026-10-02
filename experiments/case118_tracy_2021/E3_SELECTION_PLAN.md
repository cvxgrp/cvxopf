# Matched 24-hour comparison — no-solve window selection

2026-10-02. First stage of [M11 Gate E3](../../plans/milestone-11-socp.md#gate-e3--matched-24-hour-tracy-resilience-comparison).
E2 is committed at `0150b45f2`. This stage derives proposals only; it does not
launch any OPF, resume Stage D, choose extra boundary legs, or authorize execution.
Original inputs, selection and annual archives remain read-only.

## Predeclared ranking (before evaluating candidates)

Retain the four Stage D six-hour events. For each original start t0, consider
all integer 24-hour starts s with t0−18 ≤ s ≤ t0 and 0 ≤ s ≤ 8760−24.
Every candidate contains the entire event; no shard-containment or MPC padding
condition applies. There are 24 input intervals and 25 battery boundary states.

Use the accepted Stage C **lossy-DC** annual per-device boundary SoC divided by
each device's Stage A capacity. Define start/end deviations from 50% as
`d_start[k] = SoC[s,k]/capacity[k] - 0.5` and
`d_end[k] = SoC[s+24,k]/capacity[k] - 0.5`.

Rank lexicographically by:

1. `sqrt(mean(concatenate([d_start, d_end])**2))`: equal weight to each
   battery and each endpoint; dimensionless SoC fraction.
2. Maximum absolute deviation over all devices and both endpoints.
3. `abs((t0+3) - (s+12))`: distance between event and window midpoints, hours.
4. Earliest global start s.

Use unrounded scores. Equal-device weighting prevents a large fleet component
from concealing small batteries at opposite extremes. Also report capacity-
weighted fleet start/end fractions, maximum per-device deviations, every device's
endpoint fractions and all candidate scores. Fleet metrics do not select windows.
No new model objective, feasibility, solver speed or result enters this ranking.

## Evidence and review stop

Reuse `annual_results.load_results()` to verify accepted archives and inputs.
Require the annual source hashes and battery identities to match the historical
Stage D selection. Preserve its bytes and hashes; do not rerun event selection.
Retain source/table hashes, selection rule, 76 candidate dispositions (19 per
event away from year edges), and the four ranked proposals under `e3_selection/`.
Show demand/renewable/net-load context, all per-device SoC trajectories and
capacity-weighted fleet SoC, and start/end deviations for **every candidate**.
Dates use the source's fixed UTC−08:00 calendar, never DST conversion.

The proposed primary experimental boundaries remain **50% initial and hard
50% terminal energy per device**, not the annual DC endpoints or interior states.
Annual states serve as descriptive selection context only. Expose any poor
alignment; do not silently switch energy boundaries, select another event, or
pool alternative legs. Numerical threshold for "good alignment" is not claimed.

Test ranking, year-edge containment, endpoint indexing, device weighting and
input-validation paths with public synthetic arrays. No private source is needed
in CI and no optimization budget is consumed. Produce the analytical evidence,
then stop for owner acceptance of exact windows and any boundary legs. A separate
bounded runner/protocol must still be reviewed and committed before execution.
