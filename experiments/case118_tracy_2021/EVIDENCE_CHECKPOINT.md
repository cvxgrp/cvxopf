# Tracy evidence checkpoint before M11

## Disposition

On 2026-10-01, the owner closed the current evidence checkpoint and placed the
Tracy study officially on hold to prepare for M11 implementation. This closes
the present diagnostic sequence; it does not declare Stage D scientifically
complete or authorize M11 implementation in this task.

No further Tracy solves, penalty extensions, recovery trials, result insertions,
policy changes or study restarts are authorized by the prior diagnostic plan.
Resume only with new owner direction. The Tracy Stage D monitor is paused.
A read-only process check at closure found no matching Tracy Stage D or
soft-target diagnostic workers. No worker termination was needed.

## Evidence retained

| Evidence | Outcome | Retained location |
|---|---|---|
| Stage A | Approved source inputs and mapping | `stage_a/`, `STAGE_A_REPORT.md` |
| Stage B | 72 accepted DC comparison arms | `results/stage_b_maxiter5000/`, `STAGE_B_REPORT.md` |
| Stage C | Both annual DC solves independently accepted | `results/stage_c/`, `STAGE_C_REPORT.md` |
| Stage D | Execution finished; 90/96 accepted actions, 15/16 full trajectories | `results/stage_d/` |
| Quadratic soft-target sweep | Eight trials; seven passed the soft-model gate | `results/soft_target_2452_003/` |
| Bounded continuation and hard retry | Three soft wall limits; one rejected hard retry; stopped in 18.8 minutes | `results/soft_target_continuation_2452_001/` |

The unresolved Stage D case is trajectory 15, upramp, global hour 2452, W=1.
It accepted no control action; the other fifteen trajectories each accepted
all six hours. Target-free and soft-target diagnostics are not accepted
hard-target control actions. A completed execution record must not be read
as completion of all 96 requested actions.

The [soft-target report](SOFT_TARGET_SWEEP.md) contains the per-weight tables,
load shedding, summed squared target errors, timing, acceptance distinctions
and independent-audit results. The
[bounded continuation plan](SOFT_TARGET_CONTINUATION.md) remains unchanged as
the pre-run declaration. Complete assigned primals and canonical IPOPT starts,
solver logs, resource records, original results and selection decisions remain
in the diagnostic archives. Earlier failed pre-solve launch records remain
preserved separately and are excluded from scientific timing summaries.

## Scientific limits carried forward

The original hard-target recovery sequence and subsequent full-primal hard
retry did not produce an accepted solution for hour 2452/W=1. Successful
target-free and soft-target solves show feasibility only of their respective
relaxed-terminal problems. They do not settle feasibility of the original
hard-target problem.

Among independently feasible candidates, the smallest observed summed squared
terminal error was 8329.57 MWh² at the original weight-10000 point, with maximum
per-device error 66.45 MWh and shedding 355.33 MWh. The hard retry drove terminal
error much lower but violated network and device constraints; that iterate was
rejected and is not a better feasible candidate. Nonmonotonic sweep results,
inaccurate-optimal statuses, wall limits and iteration limits prevent a global
optimality or infeasibility claim.

For M11, preserve this exact failed hard-target instance and its physically
feasible soft candidates as comparison evidence. Any future relaxation result
must be interpreted for the exact modeled instance with its own validation;
these IPOPT failures are not an infeasibility certificate. No M11 design choice
or implementation is implied by closing this checkpoint.

## Preservation and review state

Original study and diagnostic result bytes were not changed during closure.
Raw `results/` data and the owner input CSV remain Git-ignored and must be
preserved separately; a source commit alone does not contain this evidence.
No raw-data relocation, cleanup, staging, commit or push was performed.

The new sweep/continuation scripts, their plans and report, and the focused
continuation test file remain uncommitted for owner review, alongside this
checkpoint and the README status update. Closure is an evidence disposition,
not a claim that those files have been reviewed or committed.
