# E1 and E2 v2 regression conclusions

Final experiment report, 2026-10-08.

## Conclusions

Both refreshed regressions passed: **18 of 18 E1 solves and four of four E2
pairs (eight solves)**. Native convergence, independent physical/accounting
checks and completed supervision passed. There were no rejected outcomes,
timeouts or E2 cycling warnings. These are the 26 evidence-producing solves,
not an accounting of every development or historical test invocation.

The current numerical representations preserve E1's frozen external-reference
agreement and E2's matched AC/SOCP engineering conclusions. Mixed-device SOCP
cost agreement is substantially tighter than in E2 v1, while generation,
battery real-power dispatch, stored energy and shedding remain practically
unchanged. Reactive dispatch can differ, notably for Case9 SOCP.

This is a correctness regression, **not evidence of a general speedup or
uniformly improved numerical accuracy**. It does not establish global AC
optimality, certify a SOCP lower bound, or make the meshed SOCP points directly
executable as AC operating points. No production change or solver tuning was
required during these runs.

## Scope and representations

The contracts are [E1's refreshed plan](E1_REGRESSION_PLAN.md) and
[E2's v2 plan](E2_V2_PLAN.md). Historical [E1](E1_REPORT.md) and
[E2](E2_REPORT.md) reports, references and raw results remain unchanged.

| Study | Cases and assembly paths | Comparison |
| --- | --- | --- |
| E1 v2 | Analytic two-bus, Case9, Case14; single-step, vectorized T=1, stepwise T=1 | Disabled preparation versus prepared SOCP; nine matched pairs |
| E2 v2 | Case9 and Case14 single-step; mixed-device two-bus T=3 vectorized and stepwise | Prepared AC versus prepared SOCP; four matched pairs |

E1 prepared SOCP selects device normalization, exact fixed-coordinate removal
and `joint5` canonical scaling, retaining original economic coordinates and
hourly objective assembly. However, the fixtures have **no optional devices
and no removed fixed coordinates**. The effective difference tested is
canonical scaling; E1 alone does not qualify device normalization, fixed
removal or cycling/shedding cost coordinates.

E2 AC uses device normalization, exact fixed removal and approved
cycling/shedding cost coordinates. Only mixed vectorized AC uses component-first
objective assembly; the other paths use hourly assembly. SOCP uses device
normalization, exact fixed removal and `joint5`, retaining original economic
coordinates and hourly assembly. Each mixed AC/SOCP formulation actually
removes one fixed coordinate. AC has two cost-coordinate bindings in vectorized
assembly and six across the three stepwise blocks; standard cases have none.

E2 retains the original physical fixtures, starts and checks. Serialized v1/v2
input hashes differ because the current schema includes numerical preparation.
The saved inputs are identical after excluding that newly introduced, disabled
default field. Historical hashes were not rewritten.

## E1: external reference agreement

All 18 calls agree with unchanged PowerModels references within the original
allowance `max(2e-5, 2e-6*abs(reference objective))`. Single-step values below
represent all three assembly paths, whose differences are negligible at that
allowance. Display values are rounded; retained records hold full precision.

| Case | Frozen reference | Baseline | Prepared |
| --- | ---: | ---: | ---: |
| Analytic two-bus | 11.122565872 | 11.122565877 | 11.122565876 |
| Case9 | 5296.666083308 | 5296.666083406 | 5296.666083535 |
| Case14 | 8075.124700267 | 8075.124700366 | 8075.124700811 |

Maximum independently reconstructed balance error was `1.142e-8 MVA`, against
`1e-4`; maximum normalized cone violation was `2.067e-10 pu²`, against `1e-6`.
Maximum native/restored objective discrepancy was `5.457e-12` objective units.
Case9 balance improved with preparation; Case14 balance became larger but
remained orders of magnitude inside the gate. Accuracy did not improve uniformly.

The analytic point remains exactly recoverable and AC-feasible under the
declared recovery checks. Case9 and Case14 points remain not directly
recoverable in both treatments and all assembly paths. Tight edge cones in
Case14 do not ensure cycle-consistent bus voltages. This concerns returned
relaxation points, not AC infeasibility. No reference regeneration or network
strengthening occurred.

## E2: matched costs and engineering behavior

The signed gap is **feasible AC physical cost minus SOCP primal physical cost**,
in objective units—not a certified global optimality gap. Both arms passed
before a pair comparison was accepted.

| Fixture | V2 AC cost | V2 SOCP cost | V1 signed gap | V2 signed gap |
| --- | ---: | ---: | ---: | ---: |
| Case9, single-step | 5296.686204 | 5296.666084 | +0.020120586 | +0.020120457 |
| Case14, single-step | 8081.526258 | 8075.124701 | +6.401557842 | +6.401557398 |
| Mixed, vectorized T=3 | 13757.789908 | 13757.789913 | −0.000999875 | −0.000005290 |
| Mixed, stepwise T=3 | 13757.789908 | 13757.789913 | +0.000001237 | −0.000004717 |

The mixed vectorized reversed gap shrank from approximately `1e-3` to `5.3e-6`.
The vectorized/stepwise SOCP cost difference shrank from `0.001001112` to
`5.730e-7`, against the unchanged allowance `0.027535580`. Both v2 gaps remain
slightly reversed. The stepwise gap was already tiny in v1 and did not improve
in absolute magnitude. This supports tolerance-based agreement, not improvement
in every metric.

The mixed fixture comprises **three half-hour intervals, totaling 1.5 hours**.
It exercises forced shedding, renewable P/Q, HVDC P, battery P/Q and hard/soft
terminal-energy policies. V2 preserves the following behavior:

- Generation is approximately 27.7453, 50 and 28.5365 MW.
- The hard-target battery charges about 12 MW, discharges 12 MW, then has
  negligible real dispatch, returning to its initial 6 MWh.
- The soft-target battery charges about 3 MW, discharges 3 MW, then charges
  about 1.748246 MW. It ends near 2.874123 MWh versus its 3 MWh target. Its
  terminal penalty is approximately 0.110915, applied once per horizon.
- ENS remains approximately 13.682 MWh; the middle interval sheds about
  30.404444% of load, with proportional reactive relief.
- HVDC input is approximately −1, −2 and −1 MW. Its 0.060000 MWh loss is
  distinct from AC network branch loss of approximately 1.888785 MWh.

AC physical cost components remain approximately 73.561581 for generation,
1.587412 for cycling, 0.530000 for HVDC, 13682.000000 for shedding and 0.110915
for terminal penalty. Duration is applied once to operating costs, with the
terminal penalty outside that integral.

Compared with v1, standard-case AC generator dispatch and voltages are unchanged.
Mixed AC real-device changes are below `1.3e-7 MW` and stored-energy changes
below `6.5e-8 MWh`. Mixed SOCP changes reach approximately 0.279 kW in generation,
0.261 kW in battery real dispatch and 0.130 kWh in stored energy: negligible for
these engineering conclusions, though not byte-identical. Case9 SOCP generator
reactive dispatch changes by up to **1.685 MVAr**, while real generation changes
by only about `2e-6 MW`. Mixed reactive device dispatch also changes. These
feasible, uncosted coordinates should not be treated as unique trajectories.

Every AC point passes direct physical checks and its rank-one lift passes
SOCP containment with complete device primals retained. Optimized SOCP points
pass the relaxation audit; mixed points are recoverable to feasible AC
candidates, whereas Case9 and Case14 are not. This matches v1. Small cost
differences alone do not establish network-physics exactness.

E2's largest optimized SOCP P/Q balance error is approximately `1.614e-7`
MW/MVAr, below the unchanged `1e-4` gate; some residuals improve and others
worsen. Mixed AC cycling auxiliary excess is about `5.9e-11` objective units,
with similarly small non-cancelling slack, below the approved advisory allowance
of about `1.016e-4`. Unexplained total-cost error is approximately `2.5–3.2e-11`.
Cycling warnings remain advisory; native convergence, physical feasibility and
unexplained accounting failures are separate acceptance requirements. No gate
was relaxed for these runs.

## Numerical effort and speed

E1 provides no speedup: native CLARABEL iterations remain 9 for the analytic
case, increase from 16 to 17 for Case9, and from 14 to 17 for Case14. Native
solve times range from fractions of a millisecond to about 1.4 ms, excluding
startup and public preparation overhead.

E2 reduces SOCP iterations for mixed cases but increases them for standard
networks. These timings are **inclusive public-solve wall time**, including
preparation/canonicalization, not E1's native-only timing.

| Fixture | SOCP iterations v1 → v2 | AC wall time v1 → v2, ms | SOCP wall time v1 → v2, ms |
| --- | ---: | ---: | ---: |
| Case9 | 16 → 17 | 50.20 → 33.01 | 11.05 → 11.81 |
| Case14 | 14 → 17 | 77.11 → 83.83 | 26.69 → 13.85 |
| Mixed vectorized | 25 → 15 | 25.99 → 32.79 | 29.06 → 30.01 |
| Mixed stepwise | 19 → 15 | 42.92 → 57.59 | 50.76 → 34.38 |

Each configuration has one observation; v1 is historical, not a contemporaneous
control. Multiple representation changes act together in E2, so these results
do not isolate a causal speed benefit. IPOPT iterations/native solve times are
unavailable through this interface and are not inferred from wall time.

## Execution and provenance

Both invocations ended normally with `matrix_complete` and no exception.
Manifests agree with independently counted archives and finalized supervision.
A result archive alone is not acceptance.

| Measure | E1 v2 | E2 v2 |
| --- | ---: | ---: |
| Accepted / finalized units | 18 / 18 calls | 4 / 4 pairs |
| Optimization calls | 18 | 8 |
| Launches / allowance | 18 / 18 | 4 / 4 |
| Result archives | 18 | 4 pair archives |
| Native archives | 18 | 8 |
| Completion manifests | 18 | 4 pair manifests plus 4 AC checkpoints |
| Finalized supervision | 18 | 4 |
| Cumulative worker seconds / allowance | 27.411 / 540 | 7.852 / 720 |
| Longest worker / wall ceiling, seconds | 1.639 / 30 | 2.132 / 180 |
| Peak sampled RSS / ceiling, MiB | 152.016 / 16384 | 179.641 / 16384 |
| Invocation elapsed, seconds | 74.145 | 21.392 |

E1 ran at clean commit `a24311f85d8649ccb7336feae38b5d4767f95e76`, from
19:03:51 to 19:05:05 UTC on 2026-10-08. E2 ran at clean commit
`7907bb94781f0245a82a109146b63ce3405b3aef`, from 19:53:22 to 19:53:44 UTC that
day; its launch session was 88334. Historical E2 v1 executed uncommitted source
identified by per-file hashes; it is not retrospectively called clean-commit.

Environment: macOS 26.7 arm64, Python 3.11.15, CVXPY 1.9.3, CLARABEL 0.11.1,
cyipopt 1.7.0, NumPy 2.4.6 and SciPy 1.17.1. CLARABEL must return `Solved`,
with maximum 200 iterations, 20 seconds and gap/feasibility tolerances `1e-9`.
E2 IPOPT must return status 0, with maximum 500 iterations, 60 CPU seconds and
tolerance/acceptable tolerance `1e-8`. These small-fixture settings do not
replace Tracy settings. No retries, fallback solves or post-result tuning occurred.

One nonblocking E2 metadata discrepancy remains: the binding's resolved AC
options list stock adapter default `print_level=3`; the prepared public wrapper
supplies `print_level=0` and `sb="yes"` when `verbose=False`. This concerns
logging, not numerical tolerances. The historical binding is preserved;
future collection should record the wrapper's effective logging options.

Raw evidence remains ignored under `results/e1_regression_001/` and
`results/e2_v2_001/`. Each retains binding/protocol, invocation start/finish,
report, and per-attempt requests, launches, phases, resources, logs, supervision
and hash-bound native/results. E2 retains the completed AC arm before SOCP.
Preserve these directories separately during archival/transfer; this tracked
report does not replace them.

SHA-256 anchors (paths relative to this report's directory):

```text
results/e1_regression_001/binding.json
edaa304d6088a27b66f0c0cc35976625e25f2e85288b7bfb507c0ff1117ee184
results/e1_regression_001/protocol.json
859385f354bafed47b76ab8d8478cbcf94b4408adcd715dd6eb1d1a35f43a96c
results/e1_regression_001/report.json
65116c7a48fabbecbfd81eab5b8428725e9f7aaad051699810cbb5453489f4e7
results/e1_regression_001/invocation-finish.json
adc2c840a770b625b3f43ff473a050adfbd827ef03a2e68ad76de5e9ec72e939

results/e2_v2_001/binding.json
57baa21ea65c08d52ba4c2196d4c854301a34fe108dd44e36cc4867ec22500ef
results/e2_v2_001/protocol.json
97774e341507a23f0e83c8b4603a66ab2aa34ef50aab83408b0105e1aa2b917f
results/e2_v2_001/report.json
29b2ef4f88e8a4d90e51135b1b38bfcb791a859d4a54208745b318b7b4dbed65
results/e2_v2_001/invocation-finish.json
05b15e3c90ed23b0088d3ca2a4967ca556a2df3122be0027c50c447548d8109c
```

Read-only `e1_compare --status` and `e2_v2 --status` require their respective
bound clean source/environment contexts. They reconstruct canonical maps and
restore retained vectors without optimization. Acceptance replay passed at the
execution checkpoints. E2 replay also passed immediately before this report;
E1 completion-manifest hashes were rechecked during report preparation. Full
E1 context-bound replay was not rerun from the later E2 commit. Do not bypass
context checks or relaunch solves to verify this report.

## Disposition

The refreshed experiments provide completed regression evidence supporting
the current representations on these fixtures. The principal observed
improvement is tighter mixed-case SOCP cost agreement—not a new dispatch
strategy, universal accuracy gain or general speedup. External reference
agreement, matched device/time accounting and AC containment remain intact;
meshed relaxation recovery limitations remain intact too.

This report closes out E1 v2 and E2 v2, not M11 automatically, and does not
authorize E3, Stage D or a broader campaign. Sources, defaults, historical
evidence and raw artifacts are unchanged. The owner retains commit control.
