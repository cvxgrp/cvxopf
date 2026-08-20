# S3 follow-up: interval-35 hard-replanning diagnostic

**Status:** protocol checkpointed; runner implementation under review; no
diagnostic solve executed

## Purpose

This is a named follow-up to the authoritative M17-S3 experiment. It does not
replace, modify, or selectively rerun the frozen baseline. Its purpose is to
determine whether the hard-target failure at controller interval 35 can be
resolved by changing only the nonlinear AC initialization.

The result gates selection of M17-S4 public policy defaults. It does not gate
the validity of the S3 observation itself.

## Scientific question and classification

The primary problem is the exact interval-35 AC window with:

- global intervals `[35, 40)`;
- realized initial SoC `515.0979097002988` MWh;
- archived replanned terminal SoC `849.9999996548939` MWh;
- the frozen Tracy load and nondispatchable inputs for those five intervals;
- the frozen device fleet and network;
- hard terminal equality; and
- the same variables, constraints, solver settings, tolerances, and
  accepted-primal gate as S3.

Any accepted solve of this exact problem proves modeled feasibility. If the
default control fails and an alternate initialization succeeds, the comparison
also demonstrates initialization-dependent local-solver behavior. An accepted
solve after changing the initial state or target only establishes feasibility
of that nearby problem. Failure under every declared initialization remains
inconclusive; it is not a proof of global infeasibility.

## Frozen evidence and integrity boundary

The source artifacts and source fingerprints are those of the authoritative
clean run recorded in `S3_RESULTS_METADATA.json`. Before reconstruction, the
loader must verify:

- equality of the frozen scenario-manifest SHA-256;
- equality of the current `src/cvxopf` Python-tree fingerprint;
- exact equality of the current `scenario.py` and `manual_runner.py` hashes;
- no change to any other source imported by model construction or the manual
  AC audit path; and
- the recorded size and SHA-256 of:

  - `frozen__hard_equality.json.gz`; and
  - `replan_every_step__hard_equality.json.gz`.

The approved post-run resume correction changed `reproduce.py`, so the
aggregate S3 artifact-execution fingerprint no longer matches. That file does
not participate in diagnostic model reconstruction. The diagnostic records
this reviewed difference explicitly and does not weaken the exact gates on the
participating model, scenario, orchestration, or audit sources. Only new
diagnostic-specific files may differ or be added. A changed participating
source stops the experiment.

The diagnostic copies the interval-35 inputs and the following scalar values
into its own manifest:

| Quantity | Frozen record | Replanned record |
|---|---:|---:|
| initial SoC at global boundary 35 | 516.7309542175291 MWh | 515.0979097002988 MWh |
| selected global-boundary-40 target | 849.9999999140263 MWh | 849.9999996548939 MWh |

The archived replanned target is the primary target for every initialization
attempt. Exactly `850.0` MWh is retained as a secondary numerical-normalization
check. The two archived targets are crossed with the two states to exclude
their `2.591324e-7` MWh difference as an explanation.

## Matched-state matrix

Each matrix problem uses the project-default flat initialization and is built
independently. No solution is reused between rows.

| Initial SoC | Endpoint | Purpose |
|---:|---:|---|
| 516.7309542175291 | 849.9999999140263 | reproduce the archived successful frozen problem |
| 515.0979097002988 | 849.9999996548939 | reproduce the archived failed replanned problem |
| 516.7309542175291 | exactly 850.0 | canonical-target normalization at the frozen state |
| 515.0979097002988 | exactly 850.0 | canonical-target normalization at the replanned state |
| 516.7309542175291 | 849.9999996548939 | replanned raw target at the frozen state |
| 515.0979097002988 | 849.9999999140263 | frozen raw target at the replanned state |

These six attempts answer whether the observed state difference or the
sub-micro-MWh target difference separates the two default-initialization
outcomes. Only the second row is the exact archived failed problem; the fourth
row is its canonical-target normalization.

## Initialization sources

Three accepted source solves are rebuilt with the frozen data so that complete
CVXPY variable states—not merely public result fields—are available:

| Source | Intervals | Initial SoC | Terminal target |
|---|---|---:|---:|
| archived frozen hard window | `[35, 40)` | 516.7309542175291 MWh | 849.9999999140263 MWh |
| archived replanned target-free diagnostic | `[35, 40)` | 515.0979097002988 MWh | none |
| archived replanned preceding hard window | `[34, 39)` | 379.5262446425310 MWh | 724.7153210174109 MWh |

The rebuilt frozen source is also the first matched-state row. The exact
replanned flat-start problem is both matched-state row two and initialization
A. Each is solved and persisted once; the runner does not create nominally
independent duplicate attempts.

Every source solve is itself audited and persisted. Reproduction requires the
same status and classification plus agreement with the authoritative artifact,
within the frozen tolerances, for objective, every public primal array, SoC
trajectory, terminal deviation, residuals, interval, initial state, target,
and device identity. Nonfinite or conditionally absent fields must agree in
availability. Exact internal variable values need not match because the model
may have nonunique solutions.

If a source solve is not accepted, dependent attempts are marked
`source_unavailable`. If it is accepted but fails the public equivalence gate,
retain it as `new_accepted_source_basin`; dependent attempts name that
classification and must not call it a reproduction of the archived source.
The runner never silently substitutes one source for another.

The numerical source-equivalence tolerances are frozen as follows:

| Quantity | Absolute tolerance | Relative tolerance |
|---|---:|---:|
| objective | `1e-4` objective units | `1e-8` |
| MW, MVAr, MVA, and MWh public arrays | `1e-4` | `1e-8` |
| voltage magnitude | `1e-7` p.u. | `1e-8` |
| voltage angle | `1e-5` degrees | `1e-8` |
| dimensionless public arrays | `1e-8` | `1e-8` |

SoC recurrence, terminal equality, balance, voltage-bound, and branch-limit
residuals must each pass their original frozen S3 acceptance tolerance. When
the corresponding authoritative residual is available, the reconstructed
value must also agree within that same absolute tolerance. Status,
classification, interval indices, storage IDs, and target-policy identity must
match exactly.

For every diagnostic attempt, construct a new interval-35 build before setting
starting values. Starting values are assigned by CVXPY variable name over
`build.prob.variables()`. A missing name, duplicate name, or shape mismatch is
an error rather than a partial warm start. The artifact retains every complete
starting array, including `null` for intentionally unset variables.

### A. Project-default flat start

Use the existing builder and CVXPY DNLP behavior. The builder assigns bus
angles to zero and voltage magnitudes to one. Immediately before
canonicalization, CVXPY initializes every remaining unset variable to the
midpoint of two finite leaf bounds, one unit inside a single finite leaf bound,
or zero when neither leaf bound is finite. Capture this completed starting
point after initialization and before IPOPT is called. Call the solver exactly
as in S3. This is the control attempt.

### B. Frozen-window state

Copy every same-named variable value from the accepted frozen interval-35
source solve into the primary replanned-state,
849.9999996548939 MWh problem. Do not alter the copied state to conceal the
1.633 MWh boundary difference. This deliberately tests whether the successful
local basin transfers to the exact failed problem.

### C. Target-free state

Copy every same-named variable value from the accepted target-free interval-35
source solve. No endpoint-affine correction is applied. This tests whether the
feasible network operating point found by the retained diagnostic supplies a
useful local basin after the hard equality is restored.

### D. Shifted preceding-window state

The interval-34 source covers `[34, 39)`. Form the interval-35 starting point as
follows:

1. The AC builder represents multistep leaves as separate variables with local
   step suffixes, for example `Pg_0` through `Pg_4`, rather than one variable
   with a leading time axis. Parse only the final `_<step>` suffix; do not infer
   time from arbitrary digits elsewhere in a component name.
2. For every step-indexed variable other than storage active power, storage
   reactive power, and SoC, copy source variables with suffixes 1–4 into
   destination variables with suffixes 0–3.
3. Initialize the new destination suffix 4 by copying the source suffix-4
   value.
4. For `b_<step>` and `b_q_<step>`, shift suffixes 1–4 in the same way and set
   the new suffix-4 value to zero.
5. Recompute `soc_0` through `soc_4` from the exact interval-35 initial SoC and
   the shifted active-power starting values using the ideal recurrence

   $$
   e_{j+1}=e_j-\Delta t\,b_j.
   $$

   The fifth zero-power row therefore holds the fourth recomputed post-step
   state.
6. Copy a variable without a recognized final step suffix by exact name and
   shape. A missing destination or ambiguous suffix is an error.

This preserves the four overlapping predicted intervals, deterministically
defines the new interval, and realigns the storage trajectory to the realized
interval-35 state. It is an initialization rule only; the optimization model
and hard terminal equality remain unchanged.

### E. Deterministic perturbations

Use the copied frozen-window state as the common center. Apply one relative
Gaussian perturbation at each of three scales. Variables are sorted by their
complete CVXPY name using Python's ordinary Unicode string ordering. Within a
variable, draw and assign scalars in column-major/Fortran order, then reshape
to the original shape in Fortran order. The RNG is NumPy 2.5.1
`numpy.random.default_rng` (PCG64).

| Attempt | Relative scale | NumPy seed |
|---|---:|---:|
| `frozen_perturb_1e-4` | `1e-4` | `17035` |
| `frozen_perturb_1e-3` | `1e-3` | `27035` |
| `frozen_perturb_1e-2` | `1e-2` | `37035` |

For each scalar entry `x`, draw `z` from NumPy's `default_rng(seed)` in stable
CVXPY variable-name order and set

$$
x^{start}=x+\epsilon\max(1,|x|)z.
$$

Use each variable's CVXPY `project()` method only to satisfy declared leaf
attributes before assignment. Do not project onto modeled equality or
inequality constraints. Record both the raw perturbation and the assigned
post-projection value. The random generator is reset once per attempt, not once
per variable.

## Solver and acceptance contract

The diagnostic freezes:

- Python 3.13.2;
- CVXPY 1.9.2;
- cyipopt 1.7.0;
- IPOPT 3.14.19;
- CLARABEL 0.11.1 as the recorded outer-plan solver context;
- the project-default IPOPT options used by S3.

The installed CVXPY IPOPT interface explicitly does not use its `warm_start`
argument. The experiment therefore does not pass or rely on that flag.
Initialization is controlled by assigning CVXPY variable values before the
DNLP reduction constructs `data["x0"]`; IPOPT then receives that vector as its
primal starting point. Before execution, an implementation test must assert
that the flattened assigned values and the actual `data["x0"]` agree exactly
in CVXPY's variable order. If that cannot be verified, the experiment stops
and records the interface limitation rather than claiming to test alternate
initializations.

Every solve uses the complete S3 accepted-primal predicate and frozen residual
tolerances. Solver `infeasible`, `user_limit`, or failure is recorded exactly
as returned and is never promoted to a feasibility conclusion.

## Attempt record

The artifact has exactly 14 canonical records:

| Record category | Count |
|---|---:|
| six matched-state rows | 6 |
| additional target-free source | 1 |
| additional preceding-window source | 1 |
| primary alternate initializations B–D | 3 |
| frozen-centered perturbations E | 3 |
| **total canonical records** | **14** |

Matched row one is the frozen source and matched row two is initialization A,
so neither is duplicated. A dependent `source_unavailable` entry still occupies
its one canonical record but records `solver_executed=false`. The summary
reports expected record count, actual record count, expected solver-call count,
actual solver-call count, and the number of attempts whose `x0` verification
passed.

Each source and diagnostic attempt records:

- problem identity: interval, initial SoC, target, and hashes of all input
  arrays;
- initialization name, parent source, transformation, seed, and scale;
- every CVXPY variable's name, shape, and complete starting value;
- variable, constraint, and parameter object identities before and after the
  solve;
- solver name/version, options, raw status, exception, iterations, setup time,
  solve time, and wall time;
- complete extracted results;
- all S3 residuals and missing/nonfinite-field checks; and
- the final scientific classification.

The diagnostic writes to a fresh ignored output directory. A small tracked
manifest will preserve source commit, clean status, source fingerprints,
software versions, artifact hashes, and the attempt summary. Artifact hashes
identify that run; they are not a promise of byte-identical solver output.

## Execution order and stopping rules

1. Checkpoint and review this protocol before implementing the runner.
2. Implement and test artifact loading, source reconstruction, initialization
   transforms, complete start capture, and accepted-primal reuse.
3. Commit the runner, return to a clean tree, and record its full SHA.
4. Run all six matched-state attempts and all primary-problem initialization
   attempts in a fresh directory without resume.
5. Do not edit model or execution source while the run is active.
6. Publish the tracked result manifest and a separate diagnostic report.

All declared attempts run even if an early initialization succeeds. No
initialization, seed, perturbation scale, solver setting, or acceptance
tolerance is selected after observing an outcome.

Larger changes to the initial SoC or endpoint constitute a separately planned
sensitivity sweep. They are not part of this feasibility diagnostic and cannot
establish feasibility of the original interval-35 problem.

## S4 policy gate

S4 default selection remains paused until this follow-up is reviewed.

- If A fails and any B–E attempt accepts the exact archived problem, the event
  is modeled-feasible and initialization-dependent. S4 must then decide whether
  explicit initialization policy belongs in the public controller.
- An accepted B–E solve proves modeled feasibility even if A was unavailable,
  but it does not prove initialization dependence. That outcome is classified
  as `modeled_feasible_alternate_initialization_incomplete_control`.
- If A accepts the exact archived problem, modeled feasibility is demonstrated,
  but the difference is classified as run-to-run or backend sensitivity rather
  than attributed to a particular alternate initialization.
- If A and B–E all fail on the exact archived problem, the event remains
  unresolved. S4 must retain explicit policy choice and audited termination;
  it must not call the target physically infeasible.
- Nearby matched-state successes or failures describe local sensitivity only.
- The independently demonstrated soft-policy recursive-feasibility failure is
  unaffected by this diagnostic.

The “A and B–E all fail” classification is available only when every declared
exact-problem initialization was constructed successfully, passed the exact
`x0` interface verification, executed, and returned no accepted primal. A
`source_unavailable` record, failed source-equivalence gate,
initialization-construction error, interface-verification failure, or missing
record makes the diagnostic `incomplete`; it is not counted as a failed solve.
An accepted but nonequivalent source basin is retained under that explicit
label and may support separately labeled exploratory attempts, but the frozen
diagnostic remains incomplete. Protocol completeness additionally requires all
14 canonical records to execute with verified `x0` values and preserved object
identities.

Feasibility evidence is reported independently of protocol completeness. An
accepted solve of the exact archived problem still proves modeled feasibility
when another record or source-equivalence gate is incomplete; the summary then
uses an explicit `*_incomplete_protocol` classification rather than suppressing
the feasibility conclusion. The incomplete-control classification above is
already explicit and is not given a redundant second suffix.
