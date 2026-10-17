# Matched 24-hour resilience comparison — execution protocol

2026-10-02. Runner implementation for review; **no E3 execution authorized**.
The owner accepted the four [selected windows](e3_selection/REPORT.md) and
selected the [separate deficit-depletion leg](E3_BOUNDARY_DECISIONS.md).
This is a standalone joint multistep comparison, not resumption of Stage D.

## Fixed mathematical study

| Event | Global input hours | Start → stop (exclusive), fixed UTC−08 |
| --- | --- | --- |
| Large surplus | [3308, 3332) | May 18 20:00 → May 19 20:00, 2021 |
| Large deficit | [1165, 1189) | February 18 13:00 → February 19 13:00, 2021 |
| Surplus → deficit | [8580, 8604) | December 24 12:00 → December 25 12:00, 2021 |
| Deficit → surplus | [2439, 2463) | April 12 15:00 → April 13 15:00, 2021 |

One 24-step hourly vectorized multistep problem per arm, with all 25 storage
boundaries retained. Use the approved Stage A fleet, identities, input arrays,
shedding permissions/prices, ratings, and Stage C economics (generator curvature
rho=1/3, ideal battery throughput cost 0.01). No resizing or reactive regularizer.
`problem.py` owns alignment; the public builders own shared device assembly.
AC/SOCP use full admittances, both-terminal apparent-power limits, and matched
options with `sparsity_tol=0`. No extra DC proxy in AC/SOCP. DC retains its existing
reactive-free semantics and separately reported objective loss proxy.

Each event has a 50%-initial, hard 50%-terminal equality per battery: 16 arms.
The large-deficit event additionally has 60%-initial, hard 25%-terminal equality
per battery: four separately labeled depletion arms. These are **20 problems**,
not 20 accepted solutions or 20 solver calls. Initial/final MWh vectors in
`binding.json` use the exact selected capacity/identity order. Interior states
have ordinary bounds/dynamics only; annual dispatch is selection context.

Order is the table's event order, energy-neutral first and depletion second
within the deficit event, then `singlenode_dc`, `lossy_dc`, `socp`, `ac` within
each leg. Each arm is independent; no preceding arm's primal is a seed.
Retain mathematical-input hashes from the E2 shared matching helper. Matching
physical inputs does not make all four network models or objectives equivalent.

## Initial bounded execution proposal

Only the **16 GiB RSS ceiling** and reuse of the AC recovery ladder have been
explicitly approved. The remaining ceilings below are proposals for runner review
and must be accepted before launch; they are ceilings, not runtime forecasts.

| Control | Initial value |
| --- | ---: |
| Worker sampled RSS ceiling | 16,384 MiB (16 GiB), fixed |
| RSS polling interval | 1 second |
| Wall ceiling per attempt, including preparation/archival | 1,800 seconds |
| Cumulative supervised worker wall budget | 43,200 seconds (12 hours) |
| Total launch ceiling, including interrupted attempts | 70 |
| Ordinary finite ladder bound | 65 calls: 15 convex + at most 50 AC |

Five launches beyond the ordinary ladder bound accommodate explicitly recorded
interruptions; they do not create new recovery roles or reopen exhausted arms.
The total worker budget may end the study before all ladders finish. The last
attempt's wall ceiling is clipped to the remaining total budget. Termination
grace/archival overhead may slightly exceed a wall ceiling; it remains charged.
Unavailable RSS monitoring stops execution, rather than disabling the ceiling.
Sampled RSS is worker RSS, not virtual memory or a guarantee about instantaneous
peaks between polls. Workers do not spawn numerical child workers; numerical
library threads are fixed to one before imports.

Convex arms use `OPFBuild.solve()`, CLARABEL, the builder's declared canonicalization
backend, `warm_start=False`, verbose logs, `tol_gap_abs=tol_gap_rel=tol_feas=1e-10`,
`max_iter=5000`, and `max_threads=1` (the retained Stage B/C settings).

AC uses the verified-complete-x0 path to `OPFBuild.solve()`: IPOPT/DNLP,
`warm_start=False`, verbose logs, `mu_strategy=adaptive`, `tol=1e-7`. Initially no
`max_iter` override: the retained upstream default is 3,000. Capture the native
version and CVXPY IPOPT interface hash; reject `ipopt.opt`. Keep the installed
interface defaults, including zero bound relaxation and exact Hessian. The
supervisor adds an explicit wall ceiling; this is not an IPOPT iteration estimate.

## AC recovery ladder

Reuse the Stage D named-variable transformations, perturbation scales/seeds,
leaf projection, SoC reconstruction and complete canonical x0 verification:

1. Hard-target cold primary.
2. Hard-target `causal_1`, `causal_2`, `causal_3` perturbations of that original
   causal start, scales `1e-4`, `1e-3`, `1e-2`.
3. Hard-target flat start. This deliberately repeats the cold start here;
   standalone arms have no earlier accepted control to shift.
4. Target-free diagnostic from the original unperturbed causal start. Only
   the terminal equality is removed; physical inputs and costs remain unchanged.
5. Hard-target copy of an independently feasible target-free point, if available.
6. Three hard-target perturbations around that point, ascending scales.

Seeds remain `17000000 + 100*global_start + 10*source_code + scale_index`, with
source_code 2 for causal and 1 for target-free. Stop at the first independently
accepted hard-target point. Failed target-free attempts skip dependent slots.
**Target-free acceptance never completes an arm or enters the matched comparison.**
No terminal softening, cross-arm seeds, SOCP-polish solves, racing or new starts.
Complete primal handoff includes voltage, angles, P/Q dispatch, device powers
and all SoC states; verify retained logical coordinates against public results.

Wall-limit outcomes consume a recovery slot. RSS/monitoring/worker exceptions
stop the invocation for operator review; no speculative continuation. Ordinary
exhausted ladders mark an arm unresolved and continue to independent later arms.
Numerical rejection, timeout and failed recovery do not prove infeasibility.

## Acceptance and retained outputs

Reuse Stage B/C accounting and Stage D AC gates unchanged: accepted solver
termination plus independent numeric physics, boxes, capability circles,
SoC recurrence/endpoints, shedding/ENS and integrated cost reconstruction.
AC allows `optimal`/`optimal_inaccurate`, not `user_limit`; DC requires `optimal`.

SOCP allows `optimal`/`optimal_inaccurate` plus a complete feasible independent
`audit_socp_relaxation`, with default declared diagnostics tolerances except
`energy=1e-4 MWh` and `fraction=1e-8`. This includes a 1e-4-MWh terminal check
in addition to the common Tracy 1e-3-MWh endpoint gate. Reuse the common device,
reporting, capability and accounting machinery; reconstruct lifted numerical
network powers, not CVXPY constraint residuals. Missing/partial/nonfinite
primals fail acceptance. Rank and voltage-recovery diagnostics are separately
retained, non-solving operations, **not SOCP acceptance requirements**.
The terminal audit tolerances differ slightly across numerical gates; a
cross-formulation objective-bound claim must re-audit both under a truly common
gate rather than infer containment from these acceptance labels.

Each attempt retains immutable request/protocol references, phase times, worker
log, sampled RSS, supervision outcome, full result/archive hash manifest,
independent audits, named/common costs, ENS, curtailment, throughput, complete
storage boundary states, available renewables, problem sizes and solver stats.
AC additionally retains assigned/causal starts and canonical x0 with layout.
SOCP retains relaxation/rank/cycle/recovered-AC diagnostics separately.
Source/version/thread/input identities are bound before execution and checked
before/after each worker. No dual certificate or global optimum is asserted.

## Restart and protocol changes

The immutable attempt journal is authoritative; `progress.json` is a disposable
cache. A restart independently reconstructs accepted archives under their
original requests and gates; it does not solve old arms again or trust a cursor.
The parent lock prevents concurrent supervisors. Live orphan PIDs are refused:
inspect and resolve them explicitly before resume. Never kill an ambiguous PID.

Interrupted attempts retry the same ladder slot in a new numbered directory;
all prior bytes and launch effort remain. Conservatively charge the full attempt
ceiling where a parent crash left unobserved elapsed time. A worker archive
without successful supervision is retained but not accepted. No continuation
of IPOPT's internal iteration state, reset of cumulative budgets, or automatic
reopening of exhausted/accepted arms. `invocations/*/finish.json` records every
orderly outcome; missing finishes indicate an interrupted parent.

Runtime protocol JSON can change wall/total/launch/poll controls, AC `tol` and an
optional positive `max_iter`, or existing convex convergence/iteration controls.
The RSS ceiling stays at 16 GiB and threads at one. `--queue-protocol` requires
an explicit reason and records an immutable, hash-linked revision with the
original execution context. It uses a separate control lock, so an operator
can queue while a worker runs. Adoption occurs **only before the next attempt**;
an active worker and its supervisor retain the original effective controls.
Already accepted results remain under their original protocol. Report mixed
revision provenance and do not describe it as an unchanged-settings experiment.
Changing a budget can shorten or extend future work, but does not reset spent
effort. Review substantive solver changes before queuing them.

No live edits to source, this document, mathematical inputs, boundary policies,
audits or the ladder. Those changes require stopping and a separately reviewed
new study/cohort; cross-source resume is refused. Runtime settings files belong
in the ignored results directory, not a dirty tracked checkout.

## Operator commands — reference only, not authorization

After review/commit, name the full clean execution commit and verify RSS access:

```sh
uv run --extra dev python -m experiments.case118_tracy_2021.run_e3 \
  --commit <full-reviewed-commit> --preflight
```

Preflight reconstructs pinned inputs and checks process-monitor access; it does
not write run artifacts, build numerical workers or solve. Initial execution
needs separate owner authorization and acknowledgement of the reviewed budget:

```sh
uv run --extra dev python -m experiments.case118_tracy_2021.run_e3 \
  --commit <full-reviewed-commit> --authorize-execution
```

Stop via Ctrl-C or `--stop --reason "operator reason"`. Resume with the same
commit and `--resume --authorize-execution`; an existing STOP marker remains
effective until explicitly acknowledged with `--acknowledge-stop`. That option
archives the marker's original bytes under `stops/`, rather than deleting it. Do not delete
attempts, binding, supervision or protocol records to resume.

Inspect with `--status` (independent read-only partial replay, no solve).
Completed archives are re-audited normally. An unfinished attempt is reported
under `active_attempt` with its request, recorded launch and latest phase when
available; missing supervision does not establish process liveness or health.
Even a completed worker archive cannot advance its arm or supply a recovery
seed until successful supervision is recorded. While that tail is unfinished,
`next` is null and `worker_seconds` includes only finalized supervision effort;
the reserved attempt is included in `launches`. Status writes nothing and does
not reconcile orphans. Resume retains strict reconciliation/replay.
To queue a
reviewed complete settings JSON while running or before resume:

```sh
uv run --extra dev python -m experiments.case118_tracy_2021.run_e3 \
  --queue-protocol experiments/case118_tracy_2021/results/e3/revised-settings.json \
  --reason "owner-reviewed change and rationale"
```

The JSON uses the exact `e3.DEFAULT_PROTOCOL` schema, not a partial patch;
`--show-protocol` prints that initial JSON without writing or solving anything.
Numerical results/logs stay ignored under `results/e3/`. Stop after disposition
of the 20 arms (including unresolved outcomes) or the declared budget; no
automatic annual extension. Retained historical evidence remains unchanged.
