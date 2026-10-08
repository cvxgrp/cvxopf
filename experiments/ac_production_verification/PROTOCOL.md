# Production-path Tracy verification

This prospective checkpoint checks the extracted AC implementation, not a new
default or a general qualification claim. No execution has been authorized yet.
All numerical calls must use public `build.solve()`; no experimental coordinate
transform, monkey-patch, private solve entry point, retry or multi-start is used.

## Four matched pairs

All inputs are the previously qualified Tracy fixture, vectorized full AC,
neutral terminal storage policy, stock deterministic start, global input hour
1165 (zero-based). Intervals are half-open.

| Pair | Input hours | Variant | Calls |
| --- | --- | --- | --- |
| 1 | [1165, 1168) | Ordinary T=3 | 1–2 |
| 2 | [1165, 1168) | Forced shedding T=3 | 3–4 |
| 3 | [1165, 1171) | Forced shedding T=6 | 5–6 |
| 4 | [1165, 1189) | Forced shedding T=24 | 7–8 |

Each pair uses normalized device limits and exact fixed-coordinate removal.
The first call retains original costs; the second enables the public
`NumericalPreparation(cost_coordinates=True)` option for both cycling and
shedding. Physical weights, demand, terminal policy, starts and IPOPT settings
are identical within a pair. The approved input-only forced-shedding multiplier
and optimistic supply witness are reused without retuning or a numerical solve.

## Evidence and interpretation

Freeze mathematical input hashes, physical starts, policy, raw source hashes,
installed numerical packages/binaries, tracked sources and this protocol.
Retain native primal/objective/status and immutable preparation evidence,
public engineering-unit trajectories, warning messages, audits, requests,
logs, phases, resource samples, completion hashes and finalized supervision.
Rebuild the installed canonical representation without optimizing to verify
the exact assigned start/layout, fixed map, native objective reconstruction,
physical restoration and publication. Trace cycling auxiliaries by identity.
Trajectory publication is compared exactly. The independently evaluated physical
and public transformed objective scalars may differ by floating-point rounding;
their publication comparison uses `1e-7 + 1e-12*abs(physical objective)`, separate
from the economic diagnostic. Retain the measured difference and limit.

Acceptance requires native IPOPT full convergence (status 0), existing physical
feasibility checks, restoration, canonical reconstruction, economic accounting,
measured forced shedding where required, provenance and normal supervised exit
within resource limits. Reconstruction uses `1e-7 + 1e-12*abs(native cost)`;
economic accuracy uses `1e-4 + 1e-6*abs(physical cost)`.
Cycling component/slack discrepancies are warnings only; subtract the signed
cycling auxiliary excess before checking unexplained total accounting. Report
non-cancelling absolute slack too. Do not loosen physics or relabel native
failures. Numerical rejection/timeout is not proof of infeasibility. Compare
physical costs only when both pair members independently qualify; unequal
local solutions/trajectories are observations, not automatic rejection.

## Execution gate and limits

First review and commit this checkpoint, then obtain separate owner approval.
`--preflight` builds/canonicalizes without optimizing or creating a run directory.
Execution requires `--authorize-execution --commit <full clean SHA>` and a fresh
directory under this experiment's ignored `results/`. No resume/overwrite.
Maximum eight launches, 180 seconds per complete worker (including audit),
16 GiB sampled RSS, 1,440 cumulative worker-seconds, one-second sampling and
one thread per numerical library. Preflight process/RSS, AC power and thermal
telemetry before launching each worker. STOP or interruption ends execution;
retain every attempt, never advance one without supervision.

Commands (use the normal project environment):

```sh
uv run --extra dev python -m experiments.ac_production_verification.run --preflight
uv run --extra dev python -m experiments.ac_production_verification.run --authorize-execution --commit <SHA>
uv run --extra dev python -m experiments.ac_production_verification.run --status
```

Historical AC/convex qualification and E3 evidence remain unchanged. E3 and
Stage D stay held; defaults, hierarchical prepared execution, SOCP tuning and
relaxation tightening are excluded.
