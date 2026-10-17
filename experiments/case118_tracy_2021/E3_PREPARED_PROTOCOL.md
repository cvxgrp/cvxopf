# Prepared E3 execution protocol

This fresh cohort runs the same 20 physical problems specified in
[the original protocol](E3_PROTOCOL.md) and [boundary decisions](E3_BOUNDARY_DECISIONS.md),
using the representations in [the prepared plan](E3_PREPARED_PLAN.md).
The original `results/e3/` and its STOP marker remain unchanged. Execution
requires a reviewed clean owner commit and separate launch approval.

## Representation and recovery

AC enables device normalization, exact fixed-coordinate removal and production
cycling/shedding cost coordinates. It runs the original ten recovery roles
with component-first objective assembly, then the same ten with hourly assembly.
Both phases use the original causal physical point and deterministic perturbation
recipe. Each phase has its own target-free result; a failed target-free diagnostic
skips only its four dependents. A target-free result never completes a hard-target
arm. An interrupted attempt retries its exact phase and slot on explicit resume.

SOCP enables device normalization, exact fixed-coordinate removal and `joint5`.
Lossy DC and copper plate enable exact removal and `joint5`, without device
normalization. All convex objectives retain original economic coordinates and
hourly assembly. The numerical policies and solver options cannot change through
runtime protocol revisions; revisions may change execution budgets explicitly.

AC retains `tol=1e-7`, adaptive barrier, cold solve, verbose logs, exact Hessian,
zero bound relaxation and installed default `max_iter=3000`. DC retains
gap absolute/relative and feasibility `1e-10`, 5,000 iterations and one thread.
SOCP uses relative gap `1e-6`, absolute gap/feasibility `1e-10`, 5,000 iterations,
one thread, minimum termination step `1e-8`, reduced gap/feasibility/infeasibility
absolute and relative `1e-10`, and reduced k/t ratio `1e-6`. All convex calls use
stock QDLDL. Binding retains resolved CLARABEL settings, AC wrapper options,
package/native-extension provenance and source hashes.

## Resources and stop conditions

The proposed initial launch ceiling is 120: at most 115 ordinary slots and five
interruption allowances. Owner launch approval must include this allowance.
The independent limits remain 43,200 cumulative supervised worker-seconds,
1,800 seconds per attempt, 16,384 MiB sampled RSS and one-second polling.
The final attempt is clipped to remaining worker time; termination overhead
is charged. The cumulative budget can end the study before all ladders finish.

Preflight and each launch require working process/RSS access, AC power and valid
thermal telemetry. Missing monitoring, RSS exhaustion, worker exceptions or
nonzero worker exit stop for review. STOP interrupts execution. Budget exhaustion
does not authorize another phase or retry. Timeouts consume their recovery slot;
native rejection and timeout do not prove physical infeasibility.

## Acceptance and retained evidence

Native full convergence is mandatory: CLARABEL `Solved` or IPOPT status 0.
Keep the original independent network/device/ENS, bounds, capability, storage
recurrence and terminal checks. Keep SOCP lifted-network tolerances, including
energy `1e-4` and fraction `1e-8`; recovered voltage is diagnostic only.

SOCP canonical-to-physical total cost uses `1e-4 + 1e-6*abs(physical total)`;
its named component gates remain unchanged. DC total accounting retains
`1e-4 + 1e-10*abs(physical total)`. AC separately reconstructs the native
canonical objective within `1e-7 + 1e-12*abs(native objective)`, and checks the
unexplained canonical-minus-physical difference after subtracting cycling
epigraph excess within `1e-4 + 1e-6*abs(physical total)`. Non-cancelling cycling
slack above `1e-4 + 1e-6*abs(physical cycling cost)` is a warning, not rejection.

Every completed attempt retains results, native vectors and preparation evidence
as separate hashed archives, plus request, physical/canonical start (for AC),
launch monitoring, phases, resources, worker log and finalized supervision.
AC archives its prospective full/reduced canonical start and maps before the
public solve. This is not proof of optimizer entry: completed preparation
evidence must confirm the identical actual verified handoff. Timed-out and
interrupted workers retain the prospective start without a completion claim.
Replay reconstructs the fresh inverse chain and physical publication without
optimization, checks maps/scales/starts and costs, and compares worker acceptance.
Unsupervised attempts cannot be accepted, advanced or used as seed sources.
Accepted hard-target arms, disposed unresolved arms, diagnostics and archive
counts must be reported separately. Comparisons require eligible accepted arms;
neither SOCP acceptance nor local AC convergence certifies a global optimum.

## Commands after review and owner commit

Use the normal project environment with all numerical thread counts set to one.
The CLI sets these counts before numerical imports. Replace `FULL_COMMIT` with
the reviewed full commit; do not execute the authorization command until approved.

```sh
uv run --offline --no-sync --extra dev python -m experiments.case118_tracy_2021.run_e3 --prepared --show-protocol
uv run --offline --no-sync --extra dev python -m experiments.case118_tracy_2021.run_e3 --prepared --commit FULL_COMMIT --preflight
uv run --offline --no-sync --extra dev python -m experiments.case118_tracy_2021.run_e3 --prepared --commit FULL_COMMIT --authorize-execution
uv run --offline --no-sync --extra dev python -m experiments.case118_tracy_2021.run_e3 --prepared --status
```

Default output is `results/e3_prepared/`. Import, protocol display and preflight
create no run artifacts and perform no optimization. Resume requires `--resume`
and the identical clean source/environment binding; clearing a prepared STOP
also requires explicit `--acknowledge-stop`. Historical E3 is not resumed.
