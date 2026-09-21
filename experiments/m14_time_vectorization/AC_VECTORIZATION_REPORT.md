# AC vectorization and Case9 Tracy comparison

AC time vectorization is implemented, reviewed and owner-accepted, with the
implementation and comparison checkpoint committed at `4b39099`. Both modes retain
`build.solve()` with `nlp=True, solver=cp.IPOPT`; no CPP/SCIPY backend is selected
for AC. Stepwise remains the default. The single-node checkpoint was approved
and committed at `6d02f09`. **The owner accepted the 168-hour stepwise AC
timeout as the bounded comparison outcome on 2026-09-19.** No longer-budget
retry is required for M14 closure; a full-week paired numerical result remains
unavailable. A subsequent owner-requested single 30-minute stepwise retry on
2026-09-20 also timed out, as recorded below.

## Implementation and DNLP shape limitation

The network builder batches lifted nodal P/Q definitions, reference angle,
terminal branch powers, both-end branch limits, and exactly one active/reactive
nodal balance over time. Components own their injections, inverter circles,
explicit AC boxes, costs and terminal policies. Source object counts depend on
the fixed network/device sizes rather than horizon length. Static inputs retain
broadcast views. Only voltage retains its established leaf bounds.

The installed CVXPY DNLP derivative engine supports at most two variable
dimensions. A direct `(nb, 1, T)` voltage/angle implementation failed in
`diff_engine/helpers.py:build_var_dict` at `d1, d2 = normalize_shape(...)`, with
`ValueError: too many values to unpack (expected 2)`. This is the characterized
exception to the original tensor proposal; no dependency patch was made.

| Variable | Shape / indexing |
|---|---|
| `theta`, `v`, `p`, `q` | `(nb, T)`; redundant voltage column axis removed |
| Sparse `P_vec`, `Q_vec` | `(nnz, T)` in existing Ybus nonzero order |
| Dense `P`, `Q` | `(nb*nb, T)`; row `i*nb+j` is bus pair `(i,j)` |
| Device power | `(n_device, T)` |
| Storage SoC | `(n_storage, T+1)` including the initial boundary |

Dense values reconstruct with `value.reshape(nb, nb, T)` in NumPy C order.
Sparse/dense short solves and independent complex-current audits pass. Public
results retain their original time-first shapes. Basic spatial indexing avoids
compound nonlinear gathers; each network term operates on all hours.

## Inputs and execution

The selected Tracy week is December 22–28, 2021, fixed UTC−08:00. The short and
intermediate cases are its first 3 and 24 hours. Inputs, Case9 mapping, costs,
fleet limits and renewable ratings match the approved single-node experiment:
common scale `315 / 1138.7624473656565`, 150 MW / 1,000 MWh storage, initial and
hard terminal SoC of 500 MWh, throughput cost 0.01, and one-hour intervals.
AC includes mapped reactive load and inverter capability. Each pair uses
identical exogenous data and devices. Lossy DC retains its loss objective term.

AC inherits Case118 `complete_flat_start` and `assign_start`, using variable
`.value` for every source variable. The shared hierarchy helpers also pass with
vectorized shapes. Independent full-horizon solves need no causal shift; the
existing hierarchy shift/SoC code remains unchanged and its regressions pass.
IPOPT defaults are retained with bounds of 1,000 iterations and 150 CPU seconds;
each worker has a 180-second wall limit.

Each point is one fresh-process observation. The full regression suite was
running during the initial ladder, so these are descriptive timings, not
isolated or repeated measurements. The final vectorized 168-hour AC arm ran
separately after the suite completed.

## Timing and memory

AC canonicalization sums DNLP reduction-chain and derivative-oracle construction
times. Solver time is wall time inside `cyipopt.Problem.solve`, including
callbacks. DC canonicalization is explicit `get_problem_data`; solver time is
CLARABEL-reported time. Interface overhead is the remaining solve wall time.
These phases do not overlap. Total includes initialization and extraction, but
excludes input preparation, imports and audits. Peak RSS covers the worker
through extraction. Exact phase measurements are retained in the JSON record.

| Network | T | Assembly | Build s | Canonicalize s | Solver s | Extract s | Total s | Peak RSS MiB |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| lossy_dc | 3 | stepwise | 0.0059 | 0.0182 | 0.0006 | 0.0001 | 0.0257 | 165.8 |
| lossy_dc | 3 | vectorized | 0.0047 | 0.0155 | 0.0004 | 0.0001 | 0.0215 | 166.5 |
| ac | 3 | stepwise | 0.0370 | 0.1376 | 0.3199 | 0.0002 | 0.5545 | 222.3 |
| ac | 3 | vectorized | 0.0181 | 0.0527 | 0.1902 | 0.0002 | 0.2740 | 186.1 |
| lossy_dc | 24 | stepwise | 0.0274 | 0.1279 | 0.0072 | 0.0009 | 0.1681 | 180.6 |
| lossy_dc | 24 | vectorized | 0.0040 | 0.0165 | 0.0047 | 0.0002 | 0.0264 | 165.6 |
| ac | 24 | stepwise | 0.3130 | 1.8493 | 8.3824 | 0.0006 | 10.8883 | 2332.6 |
| ac | 24 | vectorized | 0.0173 | 0.1009 | 2.4720 | 0.0002 | 2.6033 | 273.9 |
| lossy_dc | 168 | stepwise | 0.1913 | 1.0250 | 0.0572 | 0.0058 | 1.3064 | 275.0 |
| lossy_dc | 168 | vectorized | 0.0043 | 0.0189 | 0.0452 | 0.0002 | 0.0717 | 166.8 |
| ac | 168 | stepwise | — | — | — | — | 180 s worker-wall timeout | unavailable |
| ac | 168 | vectorized | 0.0173 | 1.7097 | 115.0275 | 0.0002 | 116.7677 | 830.5 |

The timeout is a censored worker-wall outcome, not IPOPT infeasibility or a
measured 180-second construction-to-extraction total. No phase breakdown or peak
RSS was captured for that worker. A snapshot at 91 seconds showed 10,473 MiB RSS;
this is **not** a measured peak. Vectorized AC completed in 116.77 seconds,
with a peak of 830.5 MiB.

| AC source graph at T=168 | Stepwise | Vectorized |
|---|---:|---:|
| Variable objects | 2,856 | 17 |
| Constraint objects | 16,465 | 94 |
| Parameter objects | 504 | 3 |
| Scalar variables | 25,032 | 25,033 |

Stepwise source counts were obtained by building and inspecting the same model
after the timeout; they do not imply a completed stepwise solve.

### Single longer-budget stepwise retry, 2026-09-20

The owner requested exactly one additional AC stepwise T=168 run with the
worker-wall budget increased from 180 to 1,800 seconds. IPOPT's CPU limit was
also raised from 150 to 1,800 seconds so the former CPU limit would not stop
the retry early; the 1,000-iteration limit was retained. The input data,
initialization, production sources and recorded package versions matched the
original experiment. No tests or other solves were launched alongside this run.

The retry **also reached its wall limit without a returned solution**. The
supervisor killed and reaped the worker after 1,800.41 seconds including
termination overhead. No objective, final solver status, phase timings or
measured process peak RSS were returned; this is a worker-wall timeout, not
an infeasibility result. The worker remained CPU-active in the process samples.
Sixty RSS samples at approximately 30-second intervals reached a maximum of
11,098.1 MiB (10.84 GiB), sampled at 480.45 seconds. This sampled maximum is
not a measured peak RSS.

The original timeout and all original numerical artifacts are preserved
unchanged. `AC_STEPWISE_RETRY_RESULTS.json` records the retry, pre-execution
metadata, source/artifact hashes, memory-sampling summary and retained vectorized
result. The local runner snapshot, supervisor, logs and process samples are in
`outputs/network_vectorization/stepwise_retry_2026-09-20/` and its parent.
There is still no full-week paired objective or trajectory comparison. The
vectorized result remains 116.77 seconds and 830.5 MiB measured peak RSS;
these are separate single-run observations, not a controlled speedup estimate.

## Numerical outcomes

All eleven completed solves were `optimal` and passed independent physical/cost
audits. Every residual is below `1e-4` in its stated physical or objective units.
For vectorized AC at 168 hours, real/reactive balance errors are below `1e-6`
MW/MVAr, terminal SoC error is zero, and objective is 341,477.711444.

| Network | T | Relative objective difference | Paired result |
|---|---:|---:|---|
| lossy_dc | 3 | 7.12e-12 | below 0.1% |
| ac | 3 | 8.19e-10 | below 0.1% |
| lossy_dc | 24 | 2.99e-11 | below 0.1% |
| ac | 24 | 5.62e-06 | below 0.1% |
| lossy_dc | 168 | 2.45e-11 | below 0.1% |
| ac | 168 | unavailable | stepwise timeout |

Some trajectories exceed the owner’s 0.1% investigation threshold. At 24 hours
AC differs by up to 0.0291 MW individual generation, 2.208 MW battery power,
2.208 MWh SoC, 2.233 MW renewable output, and 0.00883 pu voltage. At three hours,
individual generator and renewable reactive outputs differ by up to 134.8 and
212.9 MVAr despite nearly identical voltages and objective. Co-located devices
can redistribute unpriced reactive support while preserving net injections.
These are not represented as coordinate-wise agreement.
The owner accepted these unpriced reactive allocation differences with feasibility
and objective checks passing; reactive regularization remains future work under
M20 and is not an M14 closure requirement.

Every retained trajectory was assigned into **both actual model graphs**,
reconstructing lifted P/Q from complex voltages and evaluating all original
constraints, leaf bounds, and objectives without solving. All 22 checks passed,
including the vectorized 168-hour AC solution evaluated in the stepwise graph.
Maximum explicit constraint violation was `2.2e-08` in internal constraint units;
maximum relative objective reconstruction error was `2.32e-15`.
This supports agreement of the implemented feasible sets for the observed
solutions. It does not establish global AC optimality, uniqueness, or a completed
168-hour paired solve. No AC midpoint feasibility claim is made.

## Verification and next step

Full suite: **2,858 passed, six subtests passed**, in 188.79 seconds. Twenty new
AC tests cover sparse/dense and T=1 behavior, identities, physical power flow,
all terminal policies, nonunit time integration, active shedding, HVDC directions,
static inputs, voltage setpoints, initialization, graph growth and failure schemas.
Four additional deterministic comparison tests pass, including reversed-flow
audit rejection, disjoint phase accounting, and preservation of execution metadata
when recollecting under a changed collector context. Ruff and configured mypy pass.

The AC feature passed independent public-tool review and was owner-accepted.
The owner accepted the full-week stepwise timeout as satisfying the bounded
initial comparison requirement on 2026-09-19. No retry was required for closure.
The owner subsequently requested the single longer-budget retry documented above;
it also timed out. Both outcomes remain recorded as timeouts. The agreed M14
requirements are complete; final closure awaits one additional owner-requested
experiment, whose scope has not yet been specified. The missing full-week
stepwise numerical results remain a limitation, not a closure blocker.

The comparison module preserves existing local outcomes. Use `--collect` to
rebuild its compact record without solving. Its collector hash is separate from
the exact runner hash used for the retained solves. Raw trajectories, warnings,
and the executed runner snapshot remain in ignored `outputs/network_vectorization/`.
`check_network_trajectories.py` rebuilds source graphs for the assignment checks.

Collection reuses retained execution settings and environment metadata rather
than reading the collector's current settings. For this initial run, that metadata
was captured retrospectively: source/window/settings were reconstructed from the
executed runner and input record, and package versions were observed after the
solves during initial collection. They are not worker-attested version measurements.
Future fresh ladders capture the same metadata before launching workers. Collector
time and source hash remain separately identified.
