# Tracy AC production objective assembly qualification

Verify the committed public `NumericalPreparation.objective_assembly` option
on the difficult forced-shedding T=24 case. Both arms use public construction
and `build.solve()`. The experimental objective adapter is excluded.

## Matched comparison

| Call | Public objective assembly | Other numerical preparation |
| --- | --- | --- |
| 1 | `hourly` | Normalized device limits, exact fixed boxes, cycling and shedding cost coordinates |
| 2 | `component_first` | Identical |

Use full AC with vectorized time and P/Q assembly, zero-based input hours
`[1165,1189)`, one-hour intervals, neutral terminal storage policy and the
previously approved demand multiplier `7.5416905141946184`. Reuse the input-only
optimistic supply witness; do not retune inputs after observing a solution.
Physical economics, network/device constraints, deterministic physical start,
exact Hessian, IPOPT settings and installed libraries remain matched. Only the
public objective-assembly selector differs between policies. Grouping and the
induced canonical ordering change together, so this cannot isolate their effects.

The production implementation was approved and committed as `1f7c7f056`.
Execution additionally binds the clean full SHA containing this runner update.
This qualification does not change defaults, select a best-of result, retry a
failed arm, tune tolerances or resume E3 or Stage D.

## Provenance and checks

Before optimization, freeze physical inputs/start, both policies, expected
complete canonical starts/layouts, repository and numerical-environment hashes,
and this protocol. `--preflight` constructs and canonicalizes only and does not
create an execution directory. Bind the immutable historical records referenced
by the original diagnostic, plus the successful component-first diagnostic and
four-formulation comparison. Those records remain unchanged; replay of the new
run is not replay of historical evidence through different source bindings.

Retain request, launch, phase, log, one-second RSS samples, finalized supervision,
canonical-start archive, native/result archives and hashed completion manifest.
Check `objective_assembly` in observed production preparation evidence against
the selected arm. Expected starts are not native observations: actual retained
starts, layouts and fixed-coordinate maps must agree independently.

Reuse the [diagnostic acceptance checks](PROTOCOL.md#checks-and-retained-evidence)
without alteration: IPOPT status 0, physical feasibility and publication,
forced-shedding witness, canonical and independent component-cost accounting,
and supervised resource compliance. Cycling excess and non-cancelling slack
are warnings, not rejection gates. Unfinished attempts cannot be accepted or
advanced. Retained archives and manifests must agree before acceptance counts.
Compare costs and trajectories only when both arms independently pass; retain
one-sided and censored outcomes without claiming infeasibility or global optimality.

## Execution and resources

Fresh output is `results/production_001`, a sibling of the preserved diagnostic.
Use at most two launches, 180 seconds per entire worker including audit, 16 GiB
sampled RSS, 360 cumulative worker-seconds and one numerical thread per library.
Verify process/RSS permissions, AC power and thermal telemetry before invocation
and each launch. Missing telemetry or infrastructure failure stops execution;
no bypass is authorized by this protocol.

A finalized wall-time outcome consumes its arm and permits the next prescribed
arm. RSS limit, worker/infrastructure exception, STOP, interruption or exhausted
budget stops the invocation. `matrix_complete` means both attempts finalized,
not both accepted. No retry, resume, overwrite or ETA inferred from iterations.

```sh
uv run --extra dev python -m experiments.ac_objective_assembly.run --production-option --preflight
# After clean reviewed commit and owner execution approval:
uv run --extra dev python -m experiments.ac_objective_assembly.run --production-option --authorize-execution --commit <full SHA>
uv run --extra dev python -m experiments.ac_objective_assembly.run --production-option --status
```
