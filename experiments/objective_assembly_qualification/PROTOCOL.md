# Objective grouping across AC and convex formulations

## Question and evidence boundary

Does summing component horizon costs first help or harm the numerical solution,
compared with summing costs hourly and then integrating the horizon? This is a
representation comparison, not a change to economic weights or network physics.
It changes grouping and the induced canonical ordering together; it cannot
separate those causes or prove a universally superior representation.

The observed benefit is case-specific. The completed predecessor diagnostic
`experiments/ac_objective_assembly/results/diagnostic_001`, bound to
`40692e9022ba9a0c0d199e1a6190339cb363fac1`, records forced-shedding **T=24**,
delta=1 hour and global input interval [1165,1189). Hourly assembly reached the
180-second wall limit; component-first assembly converged in 132.94 worker
seconds, with a cycling warning. Its physical objective was 11852950486.055317.
That is not evidence of improvements on all horizons. The owner confirmed the
initial reference to a 12-hour case was a mistake: the case of interest is T=24.

Historical records remain unchanged. No production defaults or API changes,
hierarchical execution, E3/Stage D resumption, solver tuning, retries, or economic
rescaling of convex formulations are included.

## Preserved pre-worker launch failure

The approved invocation at `9032c19c83aff75255d3c2d1d574b061297b11de`,
launch session 63562, created `results/qualification_001` on 2026-10-08
at 06:38:00.857054 UTC and stopped at 06:38:03.136543 UTC with
`KeyError: 'protocol'`. The experiment wrote the limits under `limits`, while
the shared supervisor expects `protocol`. The error occurred before process
creation: no solver worker or optimizer ran, and no launch, supervision,
completion, native or result archive was written. The first attempt directory
and request exist, but are unfinished, not a numerical rejection or accepted
case. The status reader's directory-based launch count is one; actual worker
launches are zero. No numerical comparison is available from this invocation.

These records remain unchanged. The corrected runner uses the existing
supervisor's `protocol` envelope, covered by a non-solving test that exercises
the real supervisor with a simulated process. The fresh default output is
`results/qualification_002`; it must remain absent until execution. A reviewed
clean correction commit and separate launch approval are required, with the
same matrix, limits, solver settings and acceptance checks below. This is a
fresh invocation, not resumption or retry of an unfinished numerical attempt.

SHA-256 hashes of the retained pre-worker records (paths below are relative
to `results/qualification_001`):

| Record | SHA-256 |
| --- | --- |
| `binding.json` | `bc15cd109d7932e5e26f9e90d68a3e77b53c6d96100f8bb7d073bfab9f199293` |
| `protocol.json` | `cce0fe7a995a47c61961bbbf2a2111125ed9261369f5ec36d58ba1532301bc46` |
| `invocation-start.json` | `d2eb55fb81491ea78ddddb4919232d3eda6355ed99889c8fd1930e2e69ef5bde` |
| `invocation-finish.json` | `df9d93edb65e38664035268c03ea8449d463375994314fdb21ee38f0ea5eb3ff` |
| `call-001/request.json` | `8bbf1ecef9c0e9d7fd20b98ee61145c730202d1a434b9def4187bd9eced5c77c` |
| `call-001/telemetry-start.json` | `f2e2946a00dfc4700b36e9faf7ab9b18c2238611f62cde0407aafcfd53fd91f0` |

## Prespecified matrix: 16 pairs, 32 attempts

Use the existing frozen Tracy inputs starting at global hour 1165. Each row
below is repeated for AC, SOCP, lossy DC and copper plate (`singlenode_dc`).
For each formulation/fixture run hourly first, then component-first. Fixture
order is the listed order, followed by AC/SOCP/lossy DC/copper plate.

| T (hourly steps) | Input interval | Load treatment |
| --- | --- | --- |
| 3 | [1165,1168) | Ordinary |
| 3 | [1165,1168) | Forced shedding |
| 6 | [1165,1171) | Forced shedding |
| 24 | [1165,1189) | Forced shedding |

Forced shedding uses the previously reviewed input-only multiplier:
max(1, 1.1 × max(aggregate supply upper bound / hourly load)). The bound includes
full storage and renewable power ratings and ignores network/energy limits.
The identical multiplier is used within each pair. This proves required
shedding, not feasibility or optimal shedding. No input selection by outcomes.

## Fixed preparation and the sole treatment

- AC: normalized device limits, exact fixed coordinates, production cycling
  and shedding cost coordinates on; no joint conic scaling. Same stock physical
  initialization and IPOPT options in both arms.
- SOCP: normalized device limits, exact fixed coordinates, joint5 canonical
  scaling; original economic coordinates, stock cold-started CLARABEL.
- Lossy DC and copper plate: exact fixed coordinates, joint5 canonical scaling;
  original economic coordinates, stock cold-started CLARABEL.

All builds are vectorized; delta=1 hour. The hourly control is the unmodified
public build. Convex component-first assembly adds the existing integrated
generator, storage, shedding and (lossy DC only) DC-loss costs, in that order.
The AC candidate reuses the reviewed experiment-local typed-substitution
objective adapter, which emits generator + sum(cycling cost-valued absolute)
+ sum(shedding cost-valued leaf) before the installed DNLP reduction. Ordinary
physical reporting, constraints and coordinate mapping remain unchanged.
This also flattens nested sums/unit multiplications, as the successful prior
candidate did; it is not a pure change of arithmetic parenthesization.

The adapter is deliberately limited to the positive-cost, no-HVDC/no-terminal
Tracy schema. Unsupported objective terms fail explicitly. It is not promoted
to production and does not replace solver methods or global reductions. The
convex native observer temporarily wraps the stock adapter solely to capture
its unchanged input/output, exactly once; it performs no tuning or retry.

## Non-solving preparation and execution gates

The following constructs and canonicalizes every arm without invoking an
optimizer or creating results:

```sh
uv run --offline --no-sync --extra dev python -m experiments.objective_assembly_qualification.run --preflight
```

It binds raw input hashes, physical inputs, policies/options, starts, algebra
checks, canonical layouts/maps/scales and installed numerical sources.
The CLI sets all four numerical thread environment variables to 1.

Execution is a separate owner-approved step after review and a clean full
commit, with `--authorize-execution --commit <full SHA>`. A fresh direct child
of this experiment's ignored results directory is required. There is no
resume/retry mode. The supervisor checks process/RSS permission, AC power and
thermal telemetry before launch and each subsequent attempt.

Each attempt has a **180-second whole-worker ceiling**, including construction,
solve, audit and archive; sampled RSS ceiling **16 GiB**. The matrix has ceilings
of **32 launches / 5760 cumulative worker-seconds**. Single-thread numerical
execution. A finalized timeout or native numerical rejection is retained and
the matrix continues. RSS limit, worker/infrastructure exception, interrupted
supervision, operator STOP or depleted budget ends the invocation. Partial or
unsupervised attempts cannot be accepted or advanceable. No battery-driven
resumption of the held studies is authorized.

## Independent acceptance and comparison

Reuse the approved AC production-verification and corrected convex coordinate
audits, unchanged: native success (IPOPT status 0 / CLARABEL Solved), finite
restoration, represented-model physical checks, declared-box projection checks,
input-only shortage witness, native canonical objective reconstruction and
component economic accounting. SOCP's represented-model feasibility is not
rank-one/full-AC physical feasibility. AC results are local, not global optima.

Cycling epigraph differences are **warnings**, using the approved
1e-4 + 1e-6 × abs(physical component cost) accounting convention, not acceptance
rejections. Explained cycling slack is excluded from unexplained total mismatch.
Canonical reconstruction retains its separate 1e-7 + 1e-12 × abs(native cost)
gate. Convex restoration uses the declared-leaf-box policy from
`../convex_cost_qualification/COORDINATE_REAUDIT_PROTOCOL.md`, not the superseded
exact raw/public-coordinate gate. No gates are loosened for this comparison.

The worker captures public trajectories before independent native replay; replay
checks the publication against restored native physical values. Status independently
counts result/native archives, completion manifests, finalized supervision,
accepted cases, rejected candidates, warnings and timeouts. Manifests must match
retained archives; a live PID or archive alone does not establish acceptance.

Report **every pair**, including one-sided acceptance and censored timeouts.
Compare represented physical costs and whole-worker timing; do not turn a
timeout into a cost comparison or infeasibility claim. Accepted paired trajectories
are compared in engineering units: per-generator output, battery real power,
state of charge, and shedding. Differences may be nonunique schedules, not
automatic scientific failures. No iteration-derived ETA or universal-win claim.

`analysis.py` requires independent status replay before writing a fresh summary
and trajectory plots inside this run's ignored directory. It does not solve,
modify retained evidence, relabel failures or compare incomplete trajectories.
Interpret evidence by formulation and horizon before deciding any production
objective rule; qualification itself does not authorize adoption.
