# Tracy AC objective-assembly diagnostic

Preparation only. Numerical execution needs owner review, a clean committed
checkpoint, and separate approval. Production sources/defaults are unchanged.

## Question and two prespecified arms

Does replacing production's hourly cost aggregation with the earlier
component-first objective assembly change convergence of the previously
tested forced-shedding full-physics Tracy AC T=24 case?

| Call | Objective assembly | Cost-coordinate map |
| --- | --- | --- |
| 1 | Current production hourly aggregation | Production, both cycling/shedding |
| 2 | Generator horizon total + cycling horizon total + shedding horizon total | Same production map |

Both calls use vectorized full AC, input hours `[1165,1189)` (zero-based,
half-open), the approved input-only demand multiplier `7.5416905141946184`,
neutral terminal storage policy, stock deterministic physical start, normalized
device limits and exact fixed-coordinate removal. Physical weights, input data,
start, library versions, exact Hessian and IPOPT options remain matched. The
optimistic supply witness is reused; no inputs are retuned using solutions.

Both calls use public `build.solve()`. Call 2 uses an experiment-local
`cp.Minimize.tree_copy` adapter that recognizes the typed substitutions supplied
by production's coordinate mapper and returns an ordinary objective with the
three horizon totals. It reuses production's tagged abs atom, restoration,
canonicalization and fixed-coordinate machinery. No solver method or global
registry is patched. The adapter requires this positive-weight, delta=1,
three-component fixture and fails if the expected substitution changes.
It is diagnostic code, not a proposed production abstraction or default.

This comparison changes **objective summation and its induced canonical column
ordering together**. It can test the representation hypothesis but cannot
separately attribute an outcome to summation versus column ordering. Two local
solves cannot establish reliability, uniqueness, or global optimality.

## Historical motivation and immutable references

At commit `d7713602384d5c3f3b2dc73d95af58fd86a4d5bc`, AC qualification call 22
converged at iteration 88 in 138.520 worker-seconds (118.320 seconds in IPOPT).
Its physical checks passed; the 0.004628212 cycling discrepancy was a historical
strict-gate rejection and is advisory under the approved subsequent policy.
At commit `31f8fb0013a9a19109a0294752c50037d075eed8`, production verification
call 8 timed out after 180 seconds at iteration 110. Printed iterations match
through 86; production enters regularized steps at 88 instead of converging.

Read-only investigation found identical physical inputs/starts, coordinate
scales, installed libraries and settings. Production swaps the shedding block
and cycling-epigraph block and groups objective sums differently. Reordering the
retained earlier solution into production's layout gave identical constraint
expression values, with objective difference `-1.9073486328125e-6` on an
approximately `1.1853e10` total. This is evidence of equivalent mathematics and
a possible numerical sensitivity, not a proved causal explanation.

The new binding hashes relevant binding/protocol, invocation-finish, request,
supervision/log and earlier completion/result/start files in both directories.
Earlier records are neither rewritten nor relabelled. No historical replay
through a changed source binding is presented as a new accepted result.

## Checks and retained evidence

Freeze physical inputs/starts/policy, complete expected canonical starts,
identity-labelled canonical layouts, repository sources, this protocol,
numerical packages/binaries and historical references. Preflight performs
construction and canonicalization only, with no optimizer or output directory.
Tests compare control with normal production and the comparison arm with the
earlier component-first expression, aligning canonical blocks explicitly.

Workers retain request/launch/phase/log/resources, independently assembled
expected canonical start, native archive and immutable preparation evidence,
public engineering-unit trajectories, warnings and independent audits.
Expected-start files are **not observed native boundary captures**. A completed
solve must agree with the actual start/layout in production preparation evidence.
A timed-out worker has no accepted solution and may lack native/completion files.
Retain finalized supervision; check archive hashes before counting acceptance.

Acceptance checks reuse production-verification semantics: native IPOPT status
0, existing physical feasibility, restored/published trajectories, identity-bound
fixed map and start, forced-shedding witness, canonical reconstruction
`1e-7 + 1e-12*abs(native cost)`, independent economic component checks
`1e-4 + 1e-6*abs(physical component cost)`, and normal supervised exit within
resource limits. Cycling excess and non-cancelling absolute slack remain
warnings only. Subtract explained cycling excess from total accounting; do not
hide component errors under the shedding bill or loosen physics. Publication
uses exact trajectories and the existing tight roundoff rule for objective only.
Compare physical costs only if both arms independently pass.

## Execution gate and limits

Fresh directory `experiments/ac_objective_assembly/results/diagnostic_001`.
Two launches maximum; 180 seconds per entire worker including audits, 16 GiB
sampled RSS, 360 cumulative worker-seconds, one-second resource sampling, one
thread per numerical library. Verify read-only process/RSS permissions, AC power
and thermal telemetry before launching. No retry, resume or overwrite.

A **finalized wall-time outcome consumes its arm but permits the other
prespecified arm** so that a control timeout does not censor the comparison.
RSS, infrastructure/worker exception, operator STOP, interruption or exhausted
budget stops the invocation. Never advance an attempt lacking supervision.
`matrix_complete` means both attempts finalized, not that both converged.
Numerical rejection/timeout is not proof of infeasibility. No ETA from iterations.

```sh
uv run --extra dev python -m experiments.ac_objective_assembly.run --preflight
# Only after separate execution approval and a clean reviewed commit:
uv run --extra dev python -m experiments.ac_objective_assembly.run --authorize-execution --commit <full SHA>
uv run --extra dev python -m experiments.ac_objective_assembly.run --status
```

E3 and Stage D stay held. Convex formulations, tolerance changes, production
fixes/default changes, prepared hierarchy, new starts and broader qualification
are excluded. Do not run the numerical comparison during preparation or tests.
