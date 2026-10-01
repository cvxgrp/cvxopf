# Bounded soft target continuation at hour 2452

## Diagnostic plan

Use the exact Stage D trajectory 15 hour 00 W=1 problem, initial SoC and frozen
DC target from attempt 000. Preserve Stage D and the completed independent
soft-target sweep. This diagnostic changes initialization and the already
authorized quadratic terminal penalty only; it does not resume the study.

Budget: three soft solves (weights 100, 1000, 10000), then at most two original
hard-target retries. At most 1500 seconds total, 300 seconds per worker, and
8192 MiB sampled worker RSS. Reserve ten seconds of the total budget for reaping
a terminated worker. Use original Stage D IPOPT options verbatim: adaptive mu,
tol=1e-7, warm_start=False, verbose=True, default 3000 iterations. There is no
new native CPU-time option. The supervisor enforces the external wall budget.
Run sequentially, stop on implementation/monitor/RSS failure, and otherwise
retain a timed-out trial and move to the next declared trial within budget.
No extra weights, seeds or retries after this diagnostic. Stop early on an
accepted hard-target solution. Timing is descriptive, not a controlled benchmark.

Start the weight-100 solve from the accepted weight-10 sweep point. For each
weight, compare the carried incumbent, existing sweep candidate at that weight,
and newly returned point using independently reconstructed operating cost plus
weight times summed squared terminal SoC error (MWh squared). Retain the lowest
objective independently feasible candidate; an inferior or physically invalid
returned point cannot replace it. For weights 1000 and 10000, choose the start
by comparing the carried and existing same-weight candidates under the new
objective. Exact ties retain the earlier incumbent.

Independent feasibility means every original physical and accounting residual
passes its frozen tolerance, including penalty accounting. Solver status is
reported separately: a finite physically feasible iteration-limited primal may
be an incumbent but is not labeled a converged optimum. No tolerances, shedding
permissions, constraints or original hard-target acceptance gates are relaxed.

Choose the first hard-target seed by the smallest declared summed squared SoC
error across independently feasible old and new candidates, not by maximum
error or a cross-weight objective comparison. Report maximum absolute per-device
error and total energy not served alongside SSE. If the first retry fails,
use the existing weight-10000 point as a second seed only if it is materially
different: terminal SoC RMS difference exceeds 1 MWh, maximum voltage difference
exceeds 0.01 pu, or generator P or Q RMS difference exceeds 1 MW or MVAr.
If the selected best seed is that existing point, there is no duplicate retry.

Carry a complete primal, including voltage, angles, real/reactive dispatch,
storage power and SoC, nondispatchable dispatch, shedding and lifted network
coordinates. Old sweep archives omit native internal primals: reconstruct
engineering-unit coordinates explicitly (MW to pu, degrees to radians), derive
Ybus lifted P/Q entries from archived voltage and angles, and round-trip check
all archived physical fields. New archives retain every native model variable.
Do not project terminal SoC to the hard target or imply the seed initially
satisfies it. Verify and retain complete canonical IPOPT x0 including auxiliaries.

Record original request and source hashes, seed hashes, before/after contexts,
independent audits, objective comparisons and rejection decisions, starts,
solver logs, full primals, resource samples and final outcome under a fresh
results/soft_target_continuation_2452_NNN directory. Freeze this plan and code
before launching. A failed hard retry does not prove infeasibility, and any
successful diagnostic requires separate authorization before study insertion.
