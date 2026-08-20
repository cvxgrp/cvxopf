# Hierarchical battery-resilience experiment log

No experiment runs have been performed.

Protocol decisions and results should be appended chronologically. Earlier
entries should remain as time-local records when later decisions supersede
them.

## 2026-08-19 — Pre-S0 protocol review

No numerical experiment was run. The initial stub and M17 plan were reviewed
before manual-runner implementation. The draft protocol now distinguishes one
endpoint-realization solve from frozen-plan sequential execution and
closed-loop replanning; defines the indexed realized-state recurrence and
final-window truncation; requires a clean-checkout reproducibility path;
separates hard-target, target-conditioned, target-independent, and solver
failure outcomes; enables M4 `rateA` limits; and explicitly disables M19
shedding in the baseline.

The review also exposed a prerequisite not stated in the original plan:
`StorageUnitIdeal` has no stable device ID. Positional state transfer is not
acceptable for the public hierarchy. The current recommendation is a
backward-compatible optional storage ID with deterministic legacy IDs, while
requiring explicit unique IDs for multi-storage hierarchical control. This
multi-storage-only recommendation is superseded by the unconditional M17 rule
recorded below.

Open decisions before S1 freeze are the normative data path, whether hard and
soft policies remain separate or permit an explicit retained retry, the exact
outer-policy set, the storage-identity compatibility rule, and the numerical
scenario/horizon/tolerance values selected after S0 characterization.

### Follow-up decisions

The baseline will run hard and quadratic-soft terminal policies separately;
there is no automatic hard-to-soft retry. Storage identity is elevated from a
protocol detail to prerequisite device-API work immediately after S0. The
public storage dataclass will preserve positional constructor compatibility
with an appended optional ID, but every M17 participant must supply an
explicit, unique, nonempty ID. Generated labels remain build-local positional
labels and carry no cross-build identity claim.

The Tracy-derived scenario is preferred for scientific interest if its small
prepared arrays may be committed with provenance. Redistribution authority is
still to be confirmed. The exact meaning and scope of the proposed `frozen`
and `replan_every_step` outer policies also remains under discussion.

### Data decision

The project owner confirmed that the Tracy dataset was assembled from public
sources and may be used and republished at their discretion. M17 will therefore
use checked-in prepared arrays from fixed Tracy-derived windows as its
normative reproducible scenario. The committed artifact will include exact
timestamps, transformation provenance, and hashes. Reproduction will not
require the ignored raw five-year source file.

### Outer-policy decision

The initial experiment will test both `frozen` and `replan_every_step`.
`frozen` retains the original DC signposts while feeding each AC window the
realized state and serves as the less computationally intensive open-loop
benchmark. `replan_every_step` rebuilds the remaining-horizon DC plan from
every realized AC state and is the more correct MPC-like feedback policy.
Periodic replanning is not part of the baseline.

### Outer-boundary and accounting review

The protocol review identified that every shortened outer replan must retain a
terminal obligation at the original global boundary `H`; otherwise the outer
problem can rationally consume its remaining stored energy as the horizon
shrinks. The recommended normative choice is a hard, energy-neutral equality
`e_H = e_0` for every storage ID. This recommendation awaits approval.

The review also separated predicted-window diagnostics from realized
trajectory accounting. Only the executed first interval of each accepted AC
window contributes to realized cost, curtailment, loss, cycling, and later ENS
totals. Terminal penalties are planning diagnostics, outer objectives are
never summed across replans, and partial runs report completed-interval
coverage. Complete outer plans will be stored once and referenced by stable ID
rather than copied into every AC-attempt record.

### Outer terminal-policy decision

The standard outer policy is approved as a hard energy-neutral equality:
every storage device ends the global horizon at its realized initial level,
`e_H = e_0`. Every shortened replan retains that same absolute target at the
original boundary `H`. The prepared scenario will freeze the initial value;
50% of capacity is the provisional and familiar practical choice.

## 2026-08-19 — S0 characterization run

S0 added no production implementation. Ten focused tests characterized all
three formulations, single-step versus multistep `T=1`, three-step ideal SoC
dynamics, shortened replan indexing, global terminal equality, and unsolved
result behavior. The full suite passed with 1,637 tests; Ruff, strict mypy, and
the diff check also passed.

The principal indexing result is that reported `soc` contains post-step states
only. Conceptual boundary 0 is the configured initial SoC, and local boundary
`ell >= 1` is result index `ell - 1`. The run also confirmed that storage has
no published identity metadata and that result extraction has no shared
accepted-primal predicate.

The proposed manual-runner rule is to execute only `optimal` or
`optimal_inaccurate` results with complete finite required fields and accepted
physical and policy residuals. `user_limit`, exceptions, and incomplete
primals remain diagnostic outcomes. This rule awaits review before S2.

### S0 acceptance-policy decision

The proposed conservative rule is approved. A controlling action may be
executed only for raw status `optimal` or `optimal_inaccurate`, complete finite
required fields, and accepted storage, balance, voltage, thermal, and terminal
policy residuals. `user_limit`, solver exceptions, and incomplete or nonfinite
primals are retained for diagnosis but never executed. S0 is complete.

## 2026-08-19 — P1 storage identity

P1 adds an optional final `device_id` field to `StorageUnitIdeal`, preserving
the existing positional constructor. Explicit IDs are validated as unique,
nonempty strings within each fleet and are published in aligned order through
both build metadata and extracted results. Identity metadata remains available
even when no primal solution exists.

Legacy builds may continue to omit IDs. Preparation assigns collision-safe
labels derived from fleet position and publishes a parallel explicitness mask.
These labels are useful inside one build but deliberately carry no claim of
cross-build stability. The M17 runner will therefore require explicit identity
for every participating storage device and align state by ID rather than array
position. Exact fleet matching belongs at that orchestration boundary, not in
ordinary OPF construction.

The focused P1 verification passed 337 tests across all three formulations,
single-step and intentional multistep `T=1`, explicit and fallback identity,
collision handling, and unavailable-primal results. The complete suite passed
1,650 tests; Ruff, configured strict mypy, and the diff check were clean.

## 2026-08-19 — S1 scenario freeze

The normative scenario is frozen as `tracy_high_96h_v1`: 96 hourly intervals
from `2021-12-18 00:00:00-08:00` through `2021-12-21 23:00:00-08:00`. This is
the previously reviewed sustained-energy-deficit Tracy window. It contains
30,224.2 MWh of load and 7,510.0 MWh of available nondispatchable energy
before OPF curtailment. The source-to-case scale, spatial fractions, zero-noise
configuration, and renewable ratings jointly sized over the prior three-
window study are retained without retuning.

The physical and controller-independent choices are `H=96`, nominal `W=5`,
the reviewed 350 MW dispatchable fleet, fixed nonsheddable loads, seven
nondispatchable sites, and one bus-7 storage device named `battery_bus_7` with
150 MVA power, 1,000 MWh capacity, and 500 MWh initial energy. The outer
terminal policy is the approved hard equality at 500 MWh at global boundary
96. Inner hard equality and quadratic-soft policies remain separate runs; the
soft weight is the previously approved `0.05` objective units/MWh². AC branch
limits are enabled, forecasts are identical and perfect, and no fallback or
load shedding is permitted.

The checked-in prepared arrays are the normative clean-checkout inputs. The
manifest records complete device definitions, ordered identities, options,
transformations, tolerances, hashes, and preparation environment. The raw
BM-authored composite remains optional and ignored; its recorded SHA-256 is
used only by the regeneration path. This freezes the study before any manual
hierarchical result is observed.

Review identified that verified CSV frames alone would force S2 to duplicate
manifest interpretation. S1 was tightened so `load_frozen_scenario()` returns
one build-ready typed contract: verified case9 data, options, every device
fleet, aligned trajectories, and controller configuration. Case hashes and
identity/trajectory alignment are enforced by the loader itself rather than
only by tests. The residual contract now defines every maximum and formula,
uses absolute tolerances for zero-reference equalities, and treats soft
endpoint deviation as a reported outcome rather than a pass/fail threshold.

A subsequent review caught a formulation-specific distinction in that
residual contract. AC `p_net`/`q_net` are engineering-unit network injections,
so both the independently reconstructed device side and reported network side
are divided by `baseMVA`. Lossy-DC `p_net` is instead the component-injection
expression itself: device-versus-`p_net` agreement is only a MW reporting
diagnostic. The actual DC nodal audit independently reconstructs the signed
branch incidence matrix in original row order and evaluates
`(A @ p_flows + p_net) / baseMVA`. The two DC diagnostics now have separate
names and tolerances.

Eleven focused S1 tests verify clean-checkout loading, build-ready
materialization, temporal and identity
alignment, reviewed energy totals, reactive-load construction, policy and
network choices, case-array identity, and deliberate artifact-drift failure.
The complete 1,661-test suite passed; Ruff and the diff check were clean.

## 2026-08-20 — S2 manual reference runner

S2 implements the auditable experiment runner without introducing a public or
reusable controller. The runner consumes the single verified
`load_frozen_scenario()` boundary and calls only the existing public multistep
OPF builder. It retains complete outer plans once under stable IDs and links
every controlling or diagnostic AC attempt to the applicable plan, local and
global endpoint, storage-ID order, solve outcome, solver statistics, and
independently reconstructed residuals.

The implementation work first locked the asymmetric indexing rule: a frozen
plan created at global iteration zero selects local boundary `k + W_k`, while
a replan created at iteration `k` selects local boundary `W_k`; both denote
the same global endpoint. Focused synthetic tests exercise `T=3, W=2`, the
final `W=1` window, and the difference between one retained frozen plan and
one remaining-horizon plan per replan iteration. No state advances after a
failed controlling solve. A target-free diagnostic is retained for diagnosis,
but it never becomes an executed fallback.

The runner reconstructs the frozen AC and lossy-DC diagnostics rather than
using solver status alone. DC injection-reporting consistency remains distinct
from nodal balance. Realized operating totals use only accepted first
intervals, so overlapping AC predictions and repeated outer replans cannot
double-count cost, cycling, curtailment, loss, or terminal penalties.

Review then identified that the first S2 summary exposed only additive
operating totals. The output contract was completed with maximum
executed-interval voltage and both dimensional and normalized thermal
violations, cumulative absolute accepted-window signpost deviation, and total
wall time over all retained outer, controlling, and diagnostic solves. These
are calculated by the runner so S3 does not need to reconstruct protocol
semantics from raw attempts.

A second auditability review found an asymmetric failure path: sequential
execution retained an unaccepted outer plan, while endpoint realization raised
before returning its plan. Endpoint realization now returns a study-level
record in all outer-solve outcomes. An unaccepted outer plan remains fully
inspectable and is accompanied by zero AC realizations and an explicit
termination reason.

As an implementation smoke test, the frozen 96-step lossy-DC outer problem and
the first five-step hard-equality AC window both solved with accepted primals
and passed every frozen residual check. These solves were used only to verify
the runner boundary; the full endpoint and sequential S3 studies have not yet
been run or scientifically interpreted.

The endpoint-realization pair was subsequently approved as the two prior
18-hour sections `[32, 50)` and `[60, 78)`. They are frozen under the names
`crosses_saturation_boundary_32_50` and `within_regime_60_78`. The first spans
a storage saturation boundary in the inherited DC trajectory; the second
remains within one decoupled operating regime. Equal durations and common
scenario data make the boundary geometry, rather than window length or data
selection, the intended contrast.

Verification passed 33 focused S0–S2 tests and the complete 1,673-test suite.
Ruff and `git diff --check` were clean. The only warnings were the repository's
previously characterized solver/runtime and model-reporting warnings.
