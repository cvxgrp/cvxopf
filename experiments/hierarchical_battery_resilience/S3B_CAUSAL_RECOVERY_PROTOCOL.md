# S3b causal initialization-recovery experiment

**Status:** protocol complete and ready for review; no runner implemented and
no S3b solve executed

## Scientific question

The interval-35 diagnostic established existence and mechanism: the exact
target-constrained AC problem was feasible, and its returned status depended on
initialization in the frozen formulation/interface/solver stack. It did not
establish that a causal controller can reliably construct an accepted start
from information available online.

S3b asks:

> Can a predeclared causal initialization policy complete the frozen 96-hour
> `replan_every_step` hard-target trajectory using only information available
> to the controller at each iteration?

The retrospective frozen-window solution and its perturbations are prohibited.
S3b does not change the scenario, outer plans, hard terminal policy, solver
settings, acceptance gate, or executed-action rule.

## Frozen trajectory and comparison

S3b reuses `tracy_high_96h_v1` with:

- `H=96`, nominal `W=5`, and `delta=1` hour;
- the same case9 network and device fleet;
- `replan_every_step` outer planning;
- hard-equality inner SoC signposts;
- fixed nonsheddable loads;
- enforced AC `rateA` limits; and
- the S2 accepted-primal and residual gates.

The committed S3 `flat_only` hard-replanning trajectory remains the no-retry
comparison and is not rerun or revised merely to improve its outcome. S3b uses
a new ignored output directory and a separately tracked manifest and report.

## Causal information set

At controller iteration `k`, an initialization may use only:

- the realized state before interval `k`;
- the newly accepted outer plan and its selected hard SoC signpost;
- the current AC-window exogenous inputs;
- the preceding accepted target-constrained AC prediction, if `k>0`; and
- an accepted target-free solve constructed and executed at iteration `k`.

It may not use the frozen-policy trajectory, a later solve, an archived
solution from another policy, rounded targets, soft-target results, or any
result selected after observing the completed S3b trajectory.

## Attempt sequence

### First window

1. Solve the target-constrained problem from the project flat start.
2. If accepted, execute its first action and stop the window attempt sequence.
3. Otherwise solve the target-free problem from the same named model-owned flat
   start.
4. If the target-free solve is accepted, copy its complete named model-owned
   solution into a newly built target-constrained problem and solve.
5. If still unaccepted, apply the predeclared deterministic perturbation ladder
   to the approved causal source or sources in the frozen order.

### Later windows

1. Construct a causal start by shifting the preceding accepted
   target-constrained AC prediction and reconciling storage state with the newly
   realized initial SoC.
2. Solve the current target-constrained problem from that shifted start.
3. If accepted, execute its first action and stop the window attempt sequence.
4. Otherwise solve the target-free problem from the same shifted causal start.
5. If the target-free solve is accepted, copy its complete named model-owned
   solution into a newly built target-constrained problem and solve.
6. If still unaccepted, apply the predeclared deterministic perturbation ladder
   to the approved causal source or sources in the frozen order.

Every target-free solve and every target-constrained retry is a separate
retained attempt. The first accepted target-constrained attempt controls the
window. A target-free action is never executed. If the sequence is exhausted,
the runner retains the complete failed window, terminates the trajectory, and
does not advance realized state.

A window uses at most nine solver calls when target-free succeeds: one primary
target-constrained attempt, one target-free solve, one copied target-free
target-constrained attempt, and six perturbation attempts. If target-free is
not accepted, the unavailable copied/target-free-centered attempts remain
explicit dependency records but are not solver calls; the window uses at most
five solver calls.

All nine potential attempt slots are registered before the window executes so
record cardinality does not depend on outcomes. Slots after the first accepted
controlling solve are labeled `not_needed_after_acceptance`; slots whose source
was not accepted are labeled `source_unavailable`. Neither label counts as a
solver call or failed solve.

There is no target rounding, hard-to-soft fallback, load shedding, anonymous
slack, solver retuning, or use of the frozen trajectory.

## Shifted-preceding transformation

“Preceding” means the immediately preceding accepted target-constrained AC
prediction in this S3b trajectory. It is not an archived or subsequently
computed trajectory.

Let the preceding prediction cover global intervals
`[k-1, k-1+W_{k-1})` and the new window cover `[k, k+W_k)`. For every named
step variable in the new build:

- if its global interval appeared in the preceding prediction, copy the value
  from that exact global interval;
- if a new tail interval has no preceding value, hold the preceding final value
  for network, generator, and nondispatchable variables;
- initialize new-tail storage active and reactive power to zero; and
- do not copy the preceding SoC array directly.

Unsuffixed variables retain their preceding values when their names and shapes
match. Missing, extra, duplicate, or shape-incompatible named variables make
the source unavailable; they never trigger positional alignment.

Every copied or held non-SoC value is projected once through the corresponding
destination CVXPY leaf before assignment. This handles changed destination
leaf bounds deterministically, and the record retains both the raw shifted
value and projected start. SoC is not clipped or projected: the reconstructed
SoC must satisfy its destination leaf attributes within the frozen tolerance,
or the shifted source is unavailable. Explicit operating constraints may
remain violated by an initial point; only leaf-attribute validity is required
before canonicalization.

For each storage ID, begin from the newly realized energy and reconstruct every
post-step SoC using the shifted active-power start and the ideal-storage
recurrence

$$
e_{t+1}=e_t-\Delta t\,b_t.
$$

The reconstructed array must reproduce the realized boundary exactly and must
remain aligned by explicit storage identity. `W=1` and final truncated windows
use the same global-interval rule rather than a special suffix shortcut.

## Target-free copy

The target-free and target-constrained builds must have identical named
model-owned variable namespaces and shapes. An accepted target-free solution
is copied by name into a fresh target-constrained build. The target-free build
itself remains retained and unmodified.

The copied start is allowed to violate the hard terminal equality as an
initial point. The controlling solve must enforce the original unrounded hard
target, and only its accepted result may be executed.

## Deterministic perturbations

Perturbations operate on complete named model-owned arrays in sorted variable-
name order and Fortran scalar traversal order. For center value `x`, scale
`epsilon`, and standard-normal vector `z`, the raw perturbation is

$$
x^{raw}=x+\epsilon\max(1,|x|)\odot z.
$$

Each variable is projected through its CVXPY leaf projection before assignment.
The predeclared scales are `1e-4`, `1e-3`, and `1e-2`, in that order. The
perturbation-center sequence is:

1. perturb the accepted target-free solution at all three scales; then
2. perturb the original causal source—flat for the first window or shifted
   preceding thereafter—at all three scales.

If the target-free solve is not accepted, only the original causal source is
available. Attempts remain ordered first by source and then by ascending scale.

The arithmetic seed is

$$
s(k,c,j)=17{,}000{,}000+100k+10c+j,
$$

where `k` is the zero-based global controller iteration, `c=1` identifies the
accepted target-free center, `c=2` identifies the original causal flat/shifted
center, and `j` is 1, 2, or 3 for scales `1e-4`, `1e-3`, or `1e-2`,
respectively. This produces a unique, reproducible seed for every declared
trajectory attempt without relying on Python hashing or process-local state.

## Complete IPOPT starting-vector gate

Every target-constrained and target-free attempt uses the production mapping
implemented in `hard_replan_diagnostic.py`:

- retain the original named starting arrays;
- intercept the complete canonicalized `data["x0"]` mapping;
- verify every model-owned coordinate at its identified reduced offset;
- identify and retain canonicalization-added coordinates;
- retain the full vector, layout signature, and both coordinate counts; and
- verify object identity across the solve.

The frozen diagnostic's 745 model-owned and 185 auxiliary coordinates are a
five-step characterization, not hard-coded S3b requirements. Final truncated
windows must pass their dynamically generated mappings.

## Records and runtime accounting

Each attempt records:

- iteration and local/global window boundaries;
- outer-plan ID and exact terminal target;
- attempt kind and deterministic ordinal;
- source kind, source attempt ID, transformation, scale, and seed;
- named model-owned start, complete IPOPT `x0`, and layout signature;
- solver status, accepted-primal classification, residuals, iterations, and
  runtime; and
- whether the attempt supplied the executed action.

Trajectory reporting includes:

- completion and executed-interval coverage;
- total runtime;
- runtime and call counts separated into initial controlling, target-free,
  copied-target-free, and perturbation attempts;
- the number and fraction of later windows accepted on the first shifted
  attempt;
- recovery counts by successful source and perturbation scale;
- unresolved-window details;
- realized generation cost, storage cycling cost, curtailment, physical AC
  losses, voltage/thermal residuals, and signpost deviation using only executed
  actions; and
- predicted solve objectives and terminal terms as attempt diagnostics, never
  double-counted as realized cost.

## Interpretation gate

If the full trajectory completes, S3b supports `shifted_with_recovery` as an
operational policy for this frozen scenario. It does not establish a universal
default across networks, horizons, operating conditions, or solver stacks.

If the trajectory terminates, S4 exposes typed initialization sequences and
retains `flat_only` without endorsing a default recovery sequence. Exhaustion
is `unresolved_failure`; it is not a global infeasibility certificate.

## Implementation checklist

- [x] Separate feasibility evidence from causal-policy evidence.
- [x] Remove retrospective frozen-trajectory starts from the causal policy.
- [x] Freeze the first-window and later-window attempt skeletons.
- [x] Specify the shifted-preceding state reconciliation.
- [x] Preserve separate attempts and first-accepted stopping.
- [x] Exclude target rounding, soft fallback, shedding, and solver retuning.
- [x] Require production complete-`x0` verification.
- [x] Freeze trajectory and runtime reporting requirements.
- [x] Freeze the perturbation centers and order.
- [x] Freeze the arithmetic seed rule.
- [ ] Review and checkpoint this protocol.
- [ ] Implement the experiment-specific causal runner with no scientific solve
      in implementation tests.
- [ ] Review and commit the runner from a clean tree.
- [ ] Execute once into a fresh output directory.
- [ ] Publish the tracked manifest and scientific report.
