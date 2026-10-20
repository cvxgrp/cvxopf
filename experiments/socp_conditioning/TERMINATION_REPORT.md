# Three solver termination diagnostic

CLARABEL's remaining failure is a late small-step termination, not an iteration
limit. MOSEK also stalls, while COPT reports optimality but fails the unchanged
physical gate. None supplies a fully accepted solve on this diagnostic. The
CLARABEL and MOSEK retained iterates nevertheless pass all physical residual
checks; solver acceptance and numerical feasibility must remain separate.

## Matched problem and execution

One fresh, single-threaded worker per solver, serially, on the same 24-hour
fixed-load surplus problem [3308,3332). All use normalized device cones, exact
fixed-coordinate substitution and the unchanged joint scaling rule. The common
reduced model has 20,982 variables and 76,812 rows. CLARABEL receives exactly
the previously saved matrices and cone order. MOSEK/COPT require a quadratic
objective epigraph; MOSEK also dualizes. The mathematics matches, not the
solver-specific Newton systems.

CLARABEL 0.11.1 and MOSEK 11.2.5 target 1e-10 feasibility/gap tolerances;
COPT 8.0.7 uses its minimum supported FeasTol/DualTol of 1e-9. These are not
identical stopping definitions. All workers stayed below 591 MiB and five
seconds, within the frozen 4096 MiB / 180-second limits.

| Solver | Native termination | Iterations | Native solve seconds | Physical residual checks | Reconstructed physical cost |
|---|---|---:|---:|---|---:|
| CLARABEL | InsufficientProgress | 31 | 0.699 | Pass | 3.1249987630 |
| MOSEK | trm_stall / UNKNOWN | 40 | 1.076 | Pass, offline rejected-iterate audit | 3.1250145815 |
| COPT | Optimal | 39 | 1.495 | Fail | 3.1243795424 |

These timings include solver-specific presolve but exclude Python model
construction and audit. Interface times were 0.718 / 1.222 / 1.713 seconds;
supervised worker wall times were 3.677 / 3.671 / 4.731 seconds.

## What stopped CLARABEL

Changing only the final reduced-accuracy classification criteria exposed
`InsufficientProgress` instead of `AlmostSolved`. The returned x, s and z are
bit-for-bit identical to the prior fixed-substitution run. No algorithmic
rescue was applied.

At iteration 30, relative gap was 9.36e-8, absolute gap 2.93e-7,
normalized primal residual 5.80e-14, dual residual 2.53e-15 and barrier
parameter 5.94e-12. The next attempted step left the iterate unchanged.
The callback sequence ends at 30; the final record advances to 31 with
InsufficientProgress and a reported zero step. Together with the tagged
[0.11.1 solver implementation](https://raw.githubusercontent.com/oxfordcontrol/Clarabel.rs/v0.11.1/src/solver/core/solver.rs),
this identifies the small-step guard, rather than failed factorization or
iteration exhaustion, as the immediate exit path. The configured minimum
termination step is 1e-4; the log's zero is assigned at exit, not the measured
candidate step. The reason that candidate step became too small remains open.

QDLDL was used. Settings, factor sparsity, iteration diagnostics and residuals
are retained. Internal pivots, actual refinement residuals and the barrier KKT
condition number are not exposed by this Python interface. Input-matrix
conditioning therefore does not diagnose that remaining mechanism.

## What the other solvers add

MOSEK returned `rescode.trm_stall` and UNKNOWN. Its log shows nearly unchanged
values through the final several iterations, with a native task objective gap
of 4.87e-6. Saved task equality multipliers reconstruct the original primal;
their objective mapping is independently checked without another solve. Its
largest physical check relative to tolerance is active balance, 7.24e-7 MW
against 1e-4 MW. Its reconstructed physical cost differs from CLARABEL by
1.58e-5. This supports a good primal, not a certified optimum. The native log
contains the iteration table; the callback supplied no structured samples.

COPT's native primal/dual objective difference is about 9.34e-9, but its
original-unit reactive balance residual is 0.002286 MVAr (22.9 times the
limit); active balance is 0.000675 MW (6.75 times the limit). ND bounds and
normalized branch limits also fail. Canonical and reconstructed physical cost
differ by 1.36e-4. Small negative generator output contributes -0.000719 to
reconstructed generation cost, so its lower objective is not evidence of a
better feasible solution. Common-coordinate stationarity/cone violations also
prevent treating the dual objective expression as a rigorous lower bound.

The next focused intervention should target CLARABEL's late small-step behavior,
not another broad scaling sweep. A bounded, one-parameter minimum-step ablation
would distinguish an overly early safeguard from a genuinely unusable direction;
linear-system refinement/regularization remains a subsequent hypothesis. Neither
intervention has been run or adopted here.

## Retained evidence

Execution: `results/termination_probe_001/`. Each arm retains its log,
source/environment context, native evidence, immutable completion and supervision
hashes; commercial tasks are saved. Rejected MOSEK primal auditing and residual-only
classification are in a separately published offline supplement. The execution
artifacts are unchanged. `termination_analysis.py` performs no optimizer calls.

- Root summary SHA-256: `974be57ab90f36cdd9242639a0251cd10413a6e72979ae86c4119705f222dde6`.
- Verified analysis SHA-256: `39822952f202b37e4ccca3189d17a19bc410d66c89e192d8ebcc154cb629b90e`.
- Common transformed matrices SHA-256: `d4fc2a1a3d343f5bc21f06a97f5b90c98cb4282d1612f53e5ae7df1f75834127`.
- Main and isolated base commit: `dc1c5ace2d787fda563f99c0d3c11a48ba3e9b6a`.

This is uncommitted, isolated diagnostic evidence. Main production sources and
the main environment were not changed. Native acceptance was not relaxed and no
result was promoted.
