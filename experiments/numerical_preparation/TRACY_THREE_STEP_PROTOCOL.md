# Matched Tracy three-step variable-consistency diagnostic

Requested 2026-10-07: add exactly three convex solves to compare implemented
variables with the two retained full-AC candidates, not merely compare costs.
This is separate from the completed 25-call qualification, held E3, and Stage D.
No production changes, defaults, AC re-solves, retries, or solver tuning.

## Matched inputs and execution

Use the qualification fixture's Tracy deficit input hours `[1165,1168)`, T=3,
delta=1, original 27-storage fleet, rho=1/3, throughput cost 0.01, optional load
shedding, and independent neutral initial/terminal SoC=0.5 capacity. Compare all
resolved mathematical inputs after removing **only** formulation; preparation
is already stripped by `mathematical_inputs`. Pin the original qualification
binding and AC call-024/025 completion, supervision, result, and x0 hashes.
Compare against their recorded mathematical inputs, not a sliced 24-hour result.

Ordered calls: (1) SOCP `prepared_socp`, (2) lossy DC `prepared_dc`, (3)
copper plate (`singlenode_dc`) `prepared_dc`. Reuse the qualification fixture's
exact policies and resolved stock CLARABEL 0.11.1 QDLDL settings for each
formulation. These are not baseline/prepared pairs or default qualification.

Run only after independent review and the owner's clean committed checkpoint;
the owner has requested these three calls. Bind full commit, source/protocol
hashes, installed numerical packages/extensions, thread environment, and inputs.
One serial worker, one solver/library thread; 180 seconds per worker, fixed
16 GiB sampled RSS, three launches, 540 cumulative worker-seconds. Check process,
RSS, thermal telemetry and AC power before launch. Fresh ignored directory
`results/tracy_three_step_001`; never rewrite `results/qualification_001`.
Retain request/launch/phase/resources/log/supervision/completion/archive and
invocation start/finish evidence. No resume or replacement calls. STOP, battery
loss at a preflight, worker exception, or supervision failure stops the campaign.
A timeout or numerical rejection consumes its call, and is not infeasibility.
An unsupervised archive is unfinished and ineligible for acceptance.

## Variable checks and reporting

For each new solve retain the full original canonical primal reconstructed from
native reduced coordinates, positive variable scales, and exact fixed values;
retain canonical leaf layouts and restored model values. Check every original
leaf against the restored canonical slice, and independently project named
physical variables to the public time-first arrays. Pg/Qg/branch-flow raw
coordinates are per-unit; storage and ND power are MW/MVAr, SoC is MWh,
load shedding fractions are dimensionless. Preserve the initial SoC boundary
separately from the three public end-of-hour rows. Shape mismatch, missing
expected fields, nonfinite values, units, transpose, sign, identity ordering,
fixed-coordinate restoration, or canonical/public discrepancies must be visible
and fail consistency. Projection tolerance: `1e-10 + 1e-12*max(abs(expected))`,
distinct from the existing physical tolerances.

Reuse independent original-unit qualification audits without changing gates:
device bounds, renewable availability/curtailment, served/shed P/Q loads, storage
recurrence/endpoints, per-device apparent-power limits where modeled, network
balances/branch limits, ENS, and each named cost plus canonical accounting.
Native full `Solved`, independently passed audit, variable consistency, and
complete within-budget supervision are all needed for new-call acceptance.

For retained AC candidates verify manifests before reading final IPOPT x and
their captured canonical layouts. Expand combined-AC reduced coordinates with
the archived fixed map. Recheck raw-to-public projections and the independent
physical audit under current code; show the corrected canonical-cost rejection
separately from feasible physical candidate status. Historical classifications
and bytes remain untouched. This is a current non-solving assessment, not replay
under the historical source-bound runner.

Report all five candidates' complete common arrays and pairwise per-hour,
per-device deltas with ordered IDs (generator IDs where declared, otherwise
explicit generator row plus bus; storage, ND and loads use declared IDs).
Do not impose trajectory equality or an objective-equivalence gate across
different formulations. Compare component costs, served energy, curtailment,
storage power/SoC and generator dispatch, alongside all within-model checks.
Keep DC loss regularization separate from common economic costs. DC reactive
channels are absent, not zero. Copper-plate net injection is a system aggregate,
not a bus array. Lossy-DC flow proxies, AC terminal flows and SOCP lifted voltage
products represent different network models; compare only compatible meanings.
No AC recovery or global-optimality claim from a convex result. No ETA inferred
from iterations. A report may be incomplete or expose contradictions; it must
not manufacture agreement by repairing variables or relaxing tolerances.
