# Quadratic soft-target diagnostic at hour 2452

Owner requested a weight sweep after the Stage D upramp W=1 hard-target
trajectory exhausted its recovery attempts. This is a separate diagnostic,
not a continuation or modification of the frozen hard-target study.

Use trajectory 15, hour 00, attempt 000's exact initial state, device ordering,
one-hour AC model and DC-derived terminal targets. Replace only terminal
equalities with the existing two-sided quadratic storage terminal cost:
operating cost + weight * sum((final_soc_mwh - target_soc_mwh)^2).
Weights are 0, 0.01, 0.1, 1, 10, 100, 1000, 10000 in objective units/MWh^2;
zero is a target-free control. No capacity normalization or time multiplier
is applied to the boundary cost.

Each fresh worker independently uses attempt 005's successful target-free
internal logical solution, mapped through the existing start helpers. This
is a diagnostic initialization, not a causal controller continuation. No
random perturbations or successive-weight warm starts are used.

Retain source/request hashes, execution context before/after, assigned starts,
complete canonical IPOPT x0, full results, independent physics/accounting
audits, target errors, solver logs, and sampled RSS. Use the frozen physical
tolerances; soft target error is reported, not treated as a physical violation.
Reconstruct the quadratic penalty independently and subtract it only from an
audit copy of the objective, enabling the existing operating-cost audit.

Run sequentially with IPOPT adaptive mu, tolerance 1e-7, 3000 iterations,
540 CPU seconds, 600 total wall seconds and 8192 MiB sampled RSS per worker.
Complete all weights despite numerical nonconvergence; stop the sequence on
implementation/supervision failure or resource termination, preserving evidence.
Timing is descriptive and includes current machine contention, not a controlled
benchmark. No thermal warnings were reported by the preflight telemetry.

Raw output belongs in a fresh results/soft_target_2452_NNN, outside results/stage_d.
Do not insert any result into the control trajectory or change source while
the sweep runs. Small errors can motivate a subsequent hard-target retry;
positive finite-weight errors or solver failures are not infeasibility proofs.

## Completed sweep results

The eight-trial sweep completed in 935.1 seconds (15.6 minutes of summed
worker-process wall time). Seven trials passed the soft-model acceptance gate;
weight 1 reached the 3000-iteration limit. Large weights reduced target error
only partially and introduced substantial load shedding.

| Weight λ | Max target error (MWh) | Load shedding (MWh) | Solve time (s) | Solver status |
|---:|---:|---:|---:|---|
| 0 | 166.26 | <0.000001 | 77 | optimal |
| 0.01 | 160.71 | <0.000001 | 60 | optimal |
| 0.1 | 121.75 | <0.000001 | 36 | optimal |
| 1 | 118.46 | <0.000001 | 409 | user_limit |
| 10 | 78.95 | <0.000001 | 40 | optimal |
| 100 | 84.11 | <0.000001 | 106 | optimal_inaccurate |
| 1,000 | 73.78 | 39.42 | 71 | optimal_inaccurate |
| 10,000 | 66.45 | 355.33 | 120 | optimal_inaccurate |

Load shedding is total energy not served over the one-hour window, taken from
`result.energy_not_served`, not instantaneous MW or shedding cost. Values below
0.000001 MWh are displayed as a bound rather than rounded to exact zero.
Target error is the largest absolute terminal SoC deviation across storage
devices. Solve time excludes worker preparation and archival overhead.

All eight final primals passed the physical and accounting residual checks.
The weight-1 trial nevertheless failed the acceptance gate because of its
solver status; its row describes the final iterate, not an accepted optimum.
The three `optimal_inaccurate` trials passed the existing acceptance gate, which
allows that status when the independent residual checks pass. Acceptance here
is for the soft diagnostic only and does not advance Stage D control actions.

At weight 10,000, storage bus 71 remained 66.45 MWh above its target despite
355.33 MWh of load shedding. The nonmonotonic target error between weights 10
and 100, together with the inaccurate-optimal statuses at larger weights,
limits interpretation: this is not a globally optimal tradeoff curve and does
not establish hard-target infeasibility.

### Evidence

The completed run is [soft_target_2452_003](results/soft_target_2452_003/).
Each `trial-00` through `trial-07` contains `result.json.gz` with the original
result, cost decomposition, target deviations, audits, and solve time, plus
`supervision.json` with process wall time and sampled peak memory. The
[summary](results/soft_target_2452_003/summary.json) contains the other table
metrics; load shedding is read directly from each full result archive.
The [manifest](results/soft_target_2452_003/manifest.json) retains source hashes,
initialization, solver settings, and resource limits. Raw artifacts are ignored
by Git; this table is retained in the reviewable experiment report.

The earlier `soft_target_2452_001` and `soft_target_2452_002` directories retain
failed pre-solve launches caused by a diagnostic archive-callback error. They
are excluded from the numerical sweep and timing above. Original Stage D
artifacts were not changed.

## Bounded continuation and hard target retry

The follow-up diagnostic completed and stopped after 1127.5 seconds
(18.8 minutes), below the predeclared 25-minute total budget. It ran three
continuation trials and one hard-target retry. No new independently feasible
candidate was retained, and no hard-target solution was accepted. This does
not establish infeasibility of the hard-target problem.

The frozen [continuation plan](SOFT_TARGET_CONTINUATION.md) declares the budgets,
incumbent selection and second-seed criterion. Each weight compares operating
cost plus that weight times summed squared terminal error (SSE); hard-retry
seed selection instead minimizes SSE across independently feasible candidates.
The original physical constraints, shedding permissions, IPOPT settings and
hard-target acceptance gate were retained. A five-minute external worker wall
limit bounded each solve; it did not change IPOPT's original tolerance or
default 3000-iteration limit.

| Trial | New trial outcome | Retained feasible candidate | SSE (MWh²) | Max target error (MWh) | Load shedding (MWh) | New worker wall time (s) |
|---|---|---|---:|---:|---:|---:|
| λ = 100 | Wall limit | Original λ = 10 | 11,789.68 | 78.95 | <0.000001 | 300.5 |
| λ = 1,000 | Wall limit | Original λ = 1,000 | 10,503.13 | 73.78 | 39.42 | 300.3 |
| λ = 10,000 | Wall limit | Original λ = 10,000 | 8,329.57 | 66.45 | 355.33 | 300.2 |
| Original hard equality | 3000 iterations; physical audit failed | No replacement | — | — | — | 226.3 |

The soft rows report the retained incumbents, not the unfinished iterates.
All three soft trials ended by the external wall limit before a full returned
primal could be audited. Their logs show objective progress, but no printed
iterate was promoted without an independently auditable complete primal.
The λ=100 trial started from the accepted λ=10 point. At the next two weights,
the old same-weight sweep candidate beat the carried candidate under the new
objective and supplied the next start. The retained objectives were
1,189,296.10, 11,337,555.07 and 90,678,916.73, respectively. No inferior point
replaced a better incumbent.

The best independently feasible target-tracking candidate remained the original
λ=10,000 point, so it supplied the hard retry. The optional second retry was
skipped because it would duplicate that same seed. The hard solve took 224.2
seconds and returned `user_limit`. Its final iterate had SSE 0.004235 MWh²,
maximum target error 0.02865 MWh, and reported shedding 230.34 MWh, but these
are **infeasible-iterate diagnostics**, not a usable operating result. Terminal
error still exceeded the 0.001 MWh tolerance. More importantly, active/reactive
AC balance residuals were 0.4247/1.8196 pu against 0.000001 pu limits, with
additional generator, storage, renewable and branch-limit violations. Small
terminal error did not qualify this point as the best feasible candidate.

### Full primal handoff and retained evidence

All eight original candidates were independently reaudited. For the old
archives, all 18 model-variable groups were reconstructed with explicit units,
including voltage, angles, real/reactive dispatch, storage power/SoC, shedding
and lifted network variables. Physical fields round-tripped to within
3.6e-15 in their reported units; the hard seed's reconstructed Ybus active and
reactive injection discrepancies were 7.46e-12 and 1.92e-10 pu. Its terminal
SoC was not projected to the hard target: `initially_hard_target_feasible`
was explicitly false. The hard retry captured 3492 canonical IPOPT coordinates,
including 2721 model and 771 auxiliary coordinates.

The [diagnostic archive](results/soft_target_continuation_2452_001/) contains
the source-bound manifest, initial candidate audits, each complete assigned
start and canonical x0, solver logs, resource samples, objective selection
records, the hard retry's full primal/audit, and the final disposition in
[finished.json](results/soft_target_continuation_2452_001/finished.json).
Completed hard-worker before/after contexts matched. Timed-out soft workers
have no post-solve context or completed primal; their supervision records
explicitly retain termination rather than implying normal completion.
Peak sampled worker memory was below 968 MiB. The independent sweep archives
and Stage D study were not changed; no result was inserted or run restarted.

Verification included 276 baseline model/storage tests, 45 focused
continuation/Stage D tests, and a final four-test check of the complete-primal
and selection logic. The four new tests cover incumbent protection, objective
changes, SSE-based tracking selection, complete primal mapping, and separation
of physical feasibility from solver-status acceptance. The diagnostic is
finished; no further solves are scheduled by this script.
