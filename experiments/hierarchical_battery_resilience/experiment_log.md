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

## 2026-08-20 — S3 frozen manual experiment

The two endpoint-realization cases both returned accepted AC solutions with
exact inherited terminal SoC and all frozen residuals inside tolerance. The AC
interior trajectories differed from DC: the saturation-crossing case had a
maximum 2.90 MW battery-power difference and 3.44 MWh SoC difference, while
the within-regime case differed by 4.53 MW and 13.70 MWh. Crossing the
saturation boundary was therefore not an obstacle to endpoint realization in
this pair.

Both fixed-plan sequential variants completed 96 intervals. Hard equality
returned the battery from 500.0 MWh to 500.0 MWh. Quadratic soft finished at
344.4 MWh; 92 of 96 windows used material endpoint deviation, with a mean
absolute deviation of 161.5 MWh. The frozen weight is thus genuinely soft and
changes the global energy outcome rather than acting like a numerical
relaxation of equality.

The replanned hard-equality run terminated before executing interval 35. Its
new outer plan was accepted and selected 850.0 MWh at the five-hour endpoint
from a realized 515.1 MWh start. The target-conditioned AC solve returned
`infeasible`, while the retained target-free diagnostic returned an accepted
solution. This supports the frozen `target_conditioned_failure`
classification. It does not prove global infeasibility of the nonconvex AC
problem or distinguish network limitation from local solver behavior.

The replanned quadratic-soft run executed 95 intervals and reached 371.9 MWh.
The final outer problem then had to charge 128.1 MWh in one hour to retain the
500.0 MWh global terminal equality. Aggregate final-interval headroom was only
about 123.6 MW, so the outer problem was infeasible even before AC network
physics. This is a direct recursive-feasibility failure caused by accumulated
soft inner deviations.

The run therefore does not justify an unqualified policy default. Fixed hard
targets completed this deterministic case but are open-loop; hard replanning
encountered a target-conditioned AC failure; and soft replanning preserved AC
actions longer while eventually leaving the remaining outer problem
infeasible. The S4 design question is whether a remaining-horizon viability
guard belongs inside M17 or should be staged separately. No fallback, target
relaxation, solver retuning, or result-driven protocol change was applied.

The first reproduction attempt also exposed an artifact-only failure: strict
JSON rejected a retained `NaN` from an unavailable result. The writer now
encodes unavailable nonfinite values as `null`, writes atomically, and supports
resuming complete readable cases. The final artifact manifest records hashes
for every compressed raw-result file and summary table; `analysis.py` verifies
them before use.

Verification passed 40 focused M17 characterization, scenario, runner, and
artifact tests and the complete 1,680-test suite. Ruff and `git diff --check`
were clean, and the local S3 artifacts passed their recorded size and SHA-256
checks.

### S3 provenance and interpretation review

Review correctly identified that readable JSON was not sufficient for safe
resume: a modified artifact could be reused and then receive a new hash in the
final metadata. Resume validation now checks artifact schema, study and policy
identity, endpoint identities, completion and termination consistency, plan,
attempt, executed-interval, and state-history counts, and the prior size and
SHA-256 whenever completed metadata exists. Invalid cases are recomputed
rather than re-blessed.

The run context now records the Git commit and dirty state, individual hashes
of the experiment execution and artifact modules, and combined deterministic
fingerprints of all Python sources under `src/cvxopf`. Context, CSV, compressed
JSON, and final metadata writes all use temporary-file replacement. Analysis
also verifies that the run context agrees with the completed metadata.

The initial numerical run predated these provenance fields and came from an
uncommitted S3 tree based on S2 commit `52c2896`. Its internally verified
artifacts remain preliminary evidence. The authoritative run will be repeated
after checkpointing this infrastructure and returning to a clean tree.

The hard-target wording was also narrowed. A 1.63 MWh starting-state
difference separated the failed replanned window from the successful frozen
window with essentially the same target. Aggregate power, storage, generator,
reactive-generator, and thermal margins do not explain the failure, making
local solver sensitivity plausible. However, the successful solution reached
the 1.10 p.u. voltage upper bound at buses 6 and 8, so it does not establish a
fully interior feasible neighborhood. Physical infeasibility has neither been
demonstrated nor excluded; a matched-state and alternate-initialization study
is required before assigning the failure primarily to the solver.

## 2026-08-20 — Authoritative clean-source S3 run

The provenance-aware experiment infrastructure was committed as
`0cd65b1a1c809b81813389f58fde6559a161d147`. The authoritative experiment was
then run in a fresh output directory without `--resume`. `git status
--porcelain` was empty before and after execution, and no execution or model
source was edited while the run was in progress.

The clean run reproduced the preliminary scientific findings. Both endpoint
realizations were accepted with zero terminal SoC error. Both frozen policies
completed all 96 intervals; hard equality returned to 500.0 MWh, while the
quadratic-soft policy ended at 344.4 MWh. Hard stepwise replanning again
terminated at interval 35 with a target-conditioned AC failure, and soft
stepwise replanning again reached interval 95 before the remaining outer
problem became infeasible.

The tracked `S3_RESULTS_METADATA.json` is the machine-readable provenance and
summary record. It contains the execution commit and clean status, source
fingerprints, scenario hash, software versions, creation time, artifact names,
sizes and SHA-256 hashes, and trajectory-summary values. The artifact hashes
identify and verify this particular run; they are not a claim that a separate
execution will create byte-identical gzip files.

The inner solver stack was IPOPT 3.14.19 through cyipopt 1.7.0. The original
artifacts did not retain the outer solver name. Immediately after the run, the
unchanged frozen outer problem was reconstructed in the recorded environment;
it used CLARABEL 0.11.1 and reproduced the artifact's 15-iteration count. This
is recorded as a post-run identification, not direct artifact provenance.

A final resume review found two infrastructure edge cases. Validators could
raise on arbitrary decoded JSON types instead of rejecting the artifact, and a
complete audited endpoint failure with zero realizations was always recomputed.
Resume validation was made total over malformed JSON, and failed endpoint
studies now validate under their separate zero-realization contract. These
changes occurred after the authoritative run and do not change its execution
source or results.

## 2026-08-20 — Interval-35 diagnostic planning

The authoritative S3 record and post-run artifact hardening were committed
separately. The unresolved hard-replanning event will be studied before S4
selects public defaults, without altering the frozen S3 baseline.

The retained values are 516.7309542175291 MWh for the successful frozen state
and 515.0979097002988 MWh for the failed replanned state. Their recorded
targets differ by only 2.591324e-7 MWh around 850.0 MWh. Strict causal testing
therefore retains the archived replanned target `849.9999996548939` MWh for
every alternate-initialization attempt. Exactly 850.0 MWh remains a secondary
normalization check.

The proposed protocol freezes a six-row state/target matrix and exact
initialization rules for the project flat start, the successful frozen state,
the target-free state, the shifted preceding window, and three deterministic
perturbations. Only an accepted solve of the exact replanned-state and archived
target can establish modeled feasibility of the original event. Repeated
failure remains inconclusive.

## 2026-08-20 — Interval-35 runner implementation

The reviewed protocol was checkpointed in commit `426b966`. Implementation is
proceeding against a canonical 14-record registry created before any solve.
Dependency failures therefore retain explicit records rather than changing the
artifact schema or attempt count.

Code inspection confirmed that CVXPY's DNLP path preserves assigned variable
`.value` entries, completes only unset values, flattens the result in Fortran
order into `data["x0"]`, and passes that exact vector to IPOPT. The IPOPT
interface's `warm_start` argument is unused, so the experiment does not rely on
it. A synthetic DNLP test captures the actual solver-boundary `x0` and verifies
exact equality with the assigned vector.

The implementation now includes source and artifact integrity gates, public
source-equivalence classification, explicit dependency states, suffix-aware
window shifting, SoC realignment, deterministic perturbations, completeness
classification, and atomic artifact persistence. Fifteen focused tests use only
synthetic problems or checked-in fingerprints. No frozen interval-35
diagnostic problem has been solved during implementation.

## 2026-08-20 — First diagnostic invocation stopped before IPOPT

The committed runner was invoked from clean commit `71b3f7a` in the frozen
solver environment. All eight independent source/control records stopped at
the `x0` interface gate before IPOPT executed; the six dependent records were
retained as `source_unavailable`. The artifact correctly classified the run as
incomplete with 14 records, zero solver calls, and zero verified starts. It is
an infrastructure observation, not a scientific diagnostic result.

The gate had compared the 745 entries of the original CVXPY leaf variables
with IPOPT's 930-entry reduced vector. CVXPY had inserted 185 canonicalization
auxiliaries. The corrected invariant verifies every original variable in its
identified slice and independently verifies the complete reduced vector
against the reduced problem values. The full IPOPT vector and variable layout
are retained in each attempt record for audit.

The revised no-solve interception test characterizes 745 model-owned
coordinates and 185 reduction-introduced coordinates in the frozen 930-entry
IPOPT vector. All named model values map exactly to their reduced offsets. The
normalized layout signature and auxiliary starting values are deterministic
across repeated construction. When the complete model-owned starting point is
deterministically perturbed, five auxiliary coordinates also change, with a
maximum change of $10^{-5}$ in this characterization. This dependence is
captured by retaining the complete vector and coordinate map for every
attempt. The test ends through the dedicated `X0InterceptionComplete` sentinel
before the original IPOPT interface is called.

## 2026-08-20 — Authoritative interval-35 diagnostic

The reviewed runner was committed as `8dff8d1` and executed through `uv` from a
clean tree into a fresh output directory. All 14 registered solver calls ran,
all 14 production starting vectors passed the 745-model / 185-auxiliary mapping
gate, and the participating source hashes remained unchanged.

The exact archived replanned-state problem returned `infeasible` from the
project flat start. All six alternate initializations returned accepted
solutions of that same problem with zero terminal error and residuals within
the frozen gates. The exact problem is therefore modeled-feasible, and the S3
failure is initialization-dependent local-solver behavior in this frozen
formulation/interface/solver stack rather than physical infeasibility. This is
not a claim of a universal IPOPT defect.

The flat start also solved when the terminal target was rounded from
849.9999996548939 MWh to 850.0 MWh, a difference of only
$3.45\times10^{-7}$ MWh. The named starting arrays and complete 930-coordinate
IPOPT starting vector were identical between these two attempts. This is
nearby-problem sensitivity evidence; the alternate-start solves provide the
decisive evidence because they retain the exact archived target.

The diagnostic's protocol-complete flag is false. The preceding-window source
reconstruction was accepted but reached a different public solution basin than
the archived solve. Its 5.7e-14 MWh initial-state representation difference is
treated as numerically identical and is not the basis for that classification.
The basin language records an observed accepted-solution difference rather
than proving globally distinct mathematical basins. The shifted-preceding
attempt inherits that provenance. The frozen and target-free sources
reproduced, and the B, C, and E attempts independently establish exact-problem
feasibility from reproduced sources.

## 2026-08-20 — S3b causal-recovery study opened

Review of the M17 policy update identified a remaining evidence boundary. The
interval-35 diagnostic proved feasibility and initialization sensitivity, but
its frozen-window source and perturbations are retrospective and unavailable to
a causal replanned controller. S4 is therefore paused for a full-trajectory
S3b study.

The draft protocol permits only the project flat start at the first window, the
immediately preceding accepted target-constrained prediction thereafter, and
target-free solutions constructed at the current iteration. It forbids target
rounding, soft fallback, load shedding, solver retuning, and frozen-trajectory
information. The study will report both completion and the operational cost of
recovery. Perturbation centers/order and the arithmetic seed rule remain open
for review before implementation.

The perturbation decisions were subsequently closed before implementation.
The causal ladder perturbs an accepted target-free solution first and the
original causal flat/shifted start second, using scales `1e-4`, `1e-3`, and
`1e-2` for each available center. Seeds are assigned by
`17_000_000 + 100 * iteration + 10 * source_code + scale_index`, with explicit
source codes and one-based scale indices defined in the protocol. The S3b
protocol is now ready for review; no runner or scientific solve has begun.
