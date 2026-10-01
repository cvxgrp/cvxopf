# Public SOCP builder and diagnostics checkpoint

2026-10-01. Review checkpoint, **not M11 closure**. Tracy remains on hold.
Source base: `8d3eab5794f052413ad0194104b1c9cec710a7a9` plus this unstaged
implementation. No study artifacts, source-bound workers, or policies changed.

## Delivered scope

Both public build entry points accept `formulation="socp"`. The network uses
the previously tested sparse voltage-product maps, explicit squared-voltage
bounds, batched edge cones, affine full-admittance balances, and both-terminal
apparent-power cones. Shared AC-like device channels retain generator, storage,
nondispatchable, HVDC and load/shedding physics. No DC loss-cost proxy is added.
Nonzero `sparsity_tol` is rejected even with ratings disabled; nondefault
`loss_weight` is rejected. AC-specific initialization/PQ options are inert.

Routine extraction is lightweight and extends the common unavailable-primal
schema. It reports lifted values, internal pair identities, branch mappings,
`Vm_relaxed`, terminal powers and physical branch real losses, but no AC angles.
Empty topology returns empty arrays; missing nonempty values remain unavailable.

`audit_socp_relaxation` reconstructs numeric physics and device feasibility.
`recover_socp_voltage` builds a deterministic forest candidate, checks edge gaps
and cycle angles, and independently audits that candidate under AC physics while
retaining all device dispatch and storage states. Neither operation solves,
repairs, mutates the build, or accepts a control action. Missing/invalid primals
and undefined edge phases cannot become fabricated recovery successes.
Caller-supplied coupling expressions are explicitly unaudited, preventing a
full-feasibility claim. Solver statistics, including optional extra statistics,
are retained when supplied by the solver; absence is not interpreted as zero.

## Architectural acceptance checklist

| Responsibility | Concrete implementation/reuse |
| --- | --- |
| Normalization and identity alignment | Existing `problem.py` boundary; SOCP only receives aligned inputs. |
| Validation, indexing, admittances | Existing `validate_case`, reindexing, `make_branch_admittance`, and `make_ybus_matpower`. |
| Component discovery/preparation | `component_requests` and `prepare_components`, through neutral `_admittance_preparation.py`. |
| Device variables, injections, constraints | `assemble_component_vectorized`/aggregate; thin step reference uses the existing step/horizon assembly and aggregators. No copied device equations in the builder. |
| Stage and boundary costs | Existing `integrate_*` helpers and aggregated terminal cost, added once. |
| Publication | Existing component `publish_*` helpers for variables, expressions and metadata. |
| Temporal shapes | Network `ResultProjectionRegistry` merged with component projections. No alternative device reshaping. |
| Device and failed/partial results | `_initialize_results`, `_add_device_results` and existing value/projection helpers. |
| Branch conversion | Narrowly generalized `_add_branch_results`, shared by AC and SOCP, preserving partial-channel handling. |
| Explicit bounds | Existing decision registry, extended with `SOCP_VOLTAGE_SQUARED`; authority `socp_explicit_policy`, no leaf-bound qualification claim. |
| Setpoint selection | Existing shared `generator.voltage_setpoints`/binding; squared constant only differs. |
| Solving | `OPFBuild.solve`; CLARABEL, convex path, SCIPY canonicalization for vectorized graphs. |
| Diagnostics | Separate numeric implementation from authoritative inputs; never constraint `.violation()` or the builder's affine maps. |

The neutral extraction moves the existing AC case/component preparation, not
the AC formulation builder. AC imports it as its existing private parser alias;
SOCP imports the neutral helper directly. AC network equations and initialization
are unchanged. Single-step is the T=1 thin reference assembly over the same
network block and device equations, retaining ordinary single-step variable
shapes; multistep defaults to the vectorized core. There is no second network
mathematical implementation or device hierarchy rewrite.

## Bounded evidence

The predeclared [plan](PLAN.md) limits cases, horizons, solve counts and settings.
Reproduce the five observation rows with:

```sh
uv run --extra dev python experiments/m11_socp/checkpoint.py
```

Environment: macOS 26.7, arm64, Python 3.11.15, CVXPY 1.9.3, CLARABEL 0.11.1,
NumPy 2.4.6. CLARABEL `max_iter=200`, `time_limit=20` seconds; other settings
default. Static public loads, no optional devices, `delta=1`, voltage setpoints
disabled and both-terminal ratings enabled. These are observations, not a
scaling benchmark: each configuration ran once, with a concurrent regression
suite and ordinary cache/order effects. Solve-wall times follow explicit
canonicalization, so they do not include that separately measured step.

| Case | T / assembly | Variables / constraint objects | SOCs (dimension 4 / 3) | Build / canonicalize / solve wall, ms | Solver time, ms | Objective estimate |
| --- | --- | --- | --- | --- | --- | --- |
| Case9 | 1 / vectorized | 33 / 11 | 9 / 18 | 6.65 / 15.83 / 1.29 | 0.58 | 5296.666076 |
| Case9 | 3 / vectorized | 99 / 11 | 27 / 54 | 2.91 / 15.94 / 2.10 | 1.33 | 15889.998234 |
| Case9 | 3 / stepwise | 99 / 33 | 27 / 54 | 5.54 / 18.86 / 2.31 | 1.22 | 15889.998234 |
| Case14 | 1 / vectorized | 64 / 11 | 20 / 40 | 3.02 / 17.04 / 1.78 | 1.01 | 8075.124706 |
| Case14 | 3 / vectorized | 192 / 11 | 60 / 120 | 3.43 / 18.11 / 3.84 | 2.90 | 24225.374128 |

All five returned `optimal` and independently passed relaxation feasibility
at the declared tolerances. None passed exact product recovery or recovered
AC feasibility. No dual certificate was available; objectives above are
numerical relaxation optimum estimates, **not certified lower bounds**.

| Case / T / assembly | Max P/Q balance residual, MW or MVAr | Max cone violation, p.u.^2 | Max normalized determinant gap | Max cycle error, rad | Recovered max P/Q residual, MW or MVAr |
| --- | --- | --- | --- | --- | --- |
| Case9 / 1 / vectorized | 2.54e-9 | 8.41e-10 | 8.98e-3 | 5.25e-3 | 9.21 |
| Case9 / 3 / vectorized | 7.11e-13 | 5.57e-10 | 9.22e-3 | 5.25e-3 | 9.46 |
| Case9 / 3 / stepwise | 7.21e-13 | 5.56e-10 | 9.22e-3 | 5.25e-3 | 9.46 |
| Case14 / 1 / vectorized | 4.81e-11 | 2.54e-11 | 7.28e-9 | 5.87e-2 | 108.33 |
| Case14 / 3 / vectorized | 4.23e-10 | 1.06e-11 | 1.08e-8 | 5.87e-2 | 108.33 |

Case14 illustrates why edge tightness alone is insufficient: the cycle test
fails despite tiny determinant gaps. These failures concern the explicit forest
candidate, not infeasibility of AC OPF. There is no polishing or altered dispatch.

## Verification and limitations

New public and diagnostic tests cover Case9/14 single/T=1 parity, T=3 mixed
devices and non-unit delta, equality/shortfall/soft targets, reactive shedding,
identity alignment, cost integration, fixed vectorized graph-object count,
mandatory full admittance, common unsolved/partial/failed schemas, branchless
and disconnected recovery, and separate solver/feasibility/recovery outcomes.
Algebraic controls exercise rank-one non-flat voltages with tap/phase/shunt/
charging, tight inconsistent cycles, slack consistent cycles, undefined phase,
invalid primal values, and perturbed device, balance, target, rating and voltage
constraints. Audits are tested with solver and constraint-residual calls forbidden.

The first full suite completed with 3326 passed and five failures. Four were
the new branch helper's unnecessary requirement for `nl` in manually constructed
partial AC builds. That compatibility bug was fixed without changing tests;
the 47-test branch/projection/diagnostic rerun passed. The final full rerun
finished with **3330 passed, one historical failure, six subtests passed** in
284.61 seconds. All 36 new public/diagnostic tests passed. Ruff, configured mypy,
and `git diff --check` pass. Across smoke checks, targeted reruns, both full
suites and the five-row evidence script, 57 SOCP solves remained within the
predeclared 60-solve ceiling. No solver budget was increased.

The remaining historical test is
`test_retained_two_era_prefix_and_pending_start_rehearsal`: when private retained
Tracy inputs are present, it checks the current committed HEAD against the old
audit-only continuation allowlist. The already-committed SOCP work is outside
that allowlist. Both the test and production provenance safeguard are unchanged;
no private file was exposed and no test was skipped to hide it. This is not a
new SOCP numerical failure or authority to resume/alter the held study.

Follow-up: the owner authorized fixing the historical rehearsal to read the
recorded `audit-continuation-001.json` execution context instead of live HEAD
and environment. The test now checks the recorded predecessor and fails if
live context is consulted. Production guards and retained evidence are unchanged;
no new skip was added. Both continuation and runner test modules pass: **27
passed** in 23.17 seconds. The full-suite counts above describe the preceding
run; the full suite was not rerun after this test-only correction.

Still pending for M11: matched AC/SOCP comparison validation (including rejection
of thresholded AC references), matched objective-component/containment evidence,
and owner review/acceptance. This checkpoint makes no matched-bound, globally
optimal AC, hierarchy-integration, or Tracy completion claim.
