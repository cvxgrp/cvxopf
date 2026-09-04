# M14c solver characterization

## Disposition

The completed annual Case118 lossy-DC solve confirms CLARABEL as the selected
solver for this frozen study. A subsequent, explicitly non-promotional matrix
tested OSQP, SCS, and HiGHS with their default configurations on the identical
conditioned vectorized/SCIPY formulation at 24, 168, 720, and 8,760 hours.
Only one of the twelve alternative arms passed the frozen scientific audit:
HiGHS at 24 hours. None produced an accepted annual result.

The result does not establish a universal solver ranking. It characterizes one
Case118 scenario, one model representation, one machine, CVXPY 1.9.2, and the
default settings exposed through that stack. No solver was tuned and no failed
arm was retried.

The characterized solver versions are CLARABEL 0.11.1, OSQP package 1.1.3
(native banner 1.0.0), SCS 3.2.11, HiGHS 1.15.1 (`04024d7`), and MOSEK
11.2.3. These versions are part of the interpretation boundary because default
solver behavior can change between releases.

## Results

| Solver | 24 h | 168 h | 720 h | 8,760 h |
|---|---:|---:|---:|---:|
| CLARABEL | accepted, 0.16 s | accepted, 1.05 s | accepted, 5.32 s | accepted, 93.95 s |
| MOSEK | accepted, 0.20 s* | accepted, 2.21 s* | rejected, 7.35 s* | failed/UNKNOWN, 177.48 s |
| OSQP | residual rejection, 1.25 s | residual rejection, 11.33 s | residual rejection, 54.43 s | 300 s limit |
| SCS | residual rejection, 3.84 s | residual rejection, 70.27 s | 300 s limit | unusable `unbounded`, 9.57 s |
| HiGHS | accepted, 11.31 s | solver failure, 20.74 s | 300 s limit | 300 s limit |

Times are solver/interface wall times when an arm completed and supervisor wall
limits otherwise. The three starred MOSEK prefix results are historical,
unconditioned, single-thread references and are not matched comparisons. The
annual MOSEK arm used the conditioned inputs, default tolerances, and 14
threads; it ended with native status `UNKNOWN` and no usable primal.

OSQP and SCS often returned nominal solver statuses but missed the frozen
physical residual gates. At 24 hours, OSQP returned `optimal` with a maximum
nodal-balance residual of 0.00808 pu and an SoC-recurrence residual of
0.00171 MWh. SCS returned `optimal` with corresponding residuals of
3.76e-5 pu and 1.68e-4 MWh. At 720 hours, OSQP's residuals had grown to
0.216 pu and 0.0178 MWh. SCS classified the same annual problem that CLARABEL
accepted as unbounded. HiGHS reproduced the 24-hour objective and passed the
full audit, then reported the known-convex 168-hour QP as non-convex and
exceeded the five-minute limit at the longer horizons.

## What the SCIPY backend does—and does not—mean

All matrix arms say that CVXPY used its `SCIPY` canonicalization backend. That
does **not** mean every solver received the same solver-facing canonical
representation. SCIPY is the numerical expression-stuffing backend. CVXPY
still chooses a solver-specific reduction chain and decides whether to preserve
a quadratic objective from the target solver's declared capabilities.

In CVXPY 1.9.2, the solving chain sets `quad_obj` only when the solver supports
it, passes that decision through `Dcp2Cone`, and stuffs either a quadratic or
fully conic representation. See the tagged
[CVXPY solving-chain implementation][cvxpy-solving-chain]. The tagged solver
interfaces declare native quadratic-objective support for
[CLARABEL][cvxpy-clarabel-interface] and [SCS][cvxpy-scs-interface]. OSQP and
HiGHS use CVXPY's QP interfaces. By contrast, the
[MOSEK conic interface][cvxpy-mosek-interface] requires an affine objective and
then applies CVXPY's `Dualize` reduction to continuous problems.

This is the central representational distinction relevant to the observed
MOSEK behavior. The same high-level CVXPY problem and the same SCIPY stuffing
backend can still yield materially different solver inputs.

## Canonical-form evidence

The frozen conditioned 24-hour source problem has 6,004 scalar variables and
2,936 scalar equality rows, plus leaf-domain bounds. A read-only inspection of
CVXPY's generated solver data gave the following structures:

| Target | Solver-facing structure at 24 h |
|---|---|
| CLARABEL | 7,396 variables; 4,232 equalities; 12,200 nonnegative-cone coordinates; no SOCs; quadratic matrix `P` with 5,544 nonzeros |
| SCS | Same dimensions and quadratic data as CLARABEL: 7,396 variables, 16,432 rows, 25,648 `A` nonzeros, 5,544 `P` nonzeros, and no SOCs |
| OSQP | Native QP with 7,396 variables, 4,232 equalities, 12,200 inequalities, and the quadratic objective retained |
| HiGHS | Native QP with 7,396 variables and 4,424 rows; CVXPY retains leaf bounds as solver-native variable bounds, leaving only 192 explicit inequalities |
| MOSEK, before CVXPY dualization | 11,860 variables; 2,936 equalities; 12,200 nonnegative coordinates; 5,760 three-dimensional SOCs |
| MOSEK, after CVXPY dualization | 11,860 affine rows and 32,416 conic coordinates |

The inspection rebuilt the frozen conditioned vectorized 24-hour problem and
called CVXPY's solver-data reductions with `canon_backend="SCIPY"`. For MOSEK,
the intermediate conic program was inspected immediately before and after the
MOSEK interface's `Dualize` reduction. These diagnostic counts explain the
representation but are not promoted experimental results and do not modify the
matrix's frozen acceptance decisions.

The SCS dimensions are independently visible in the retained
[24-hour SCS worker log][scs-24-log], and the HiGHS dimensions in the
[24-hour HiGHS worker log][highs-24-log]. The 24-hour native-QP matrix is
diagonal, with 5,544 positive diagonal entries and 1,852 zero directions. Its
positive entries range from 0.0002 to 0.197. Thus the generator
regularization makes the relevant dispatch directions strictly quadratic, but
the full solver coordinate space remains positive semidefinite rather than
positive definite.

The same representation split becomes much larger at the annual horizon. The
annual high-level problem contained 2,190,004 scalar variables and 1,068,728
scalar equality rows, plus leaf-domain bounds. The retained
[annual MOSEK log][mosek-annual-log] records the following solver task after
CVXPY canonicalization:

- 11,826,016 scalar variables;
- 4,327,444 constraints; and
- 2,102,400 cones.

After MOSEK presolve, 8,827,206 scalar variables, 3,059,425 constraints, and
2,102,401 cones remained; 6,147,695 of the variables were conic. For
comparison, the quadratic CLARABEL/SCS representation at 8,760 hours has
2,698,084 canonical variables, 5,991,856 rows, 2,023,560 `P` nonzeros, and
9,355,696 `A` nonzeros, as recorded in the
[annual SCS log][scs-annual-log]. These counts are not directly interchangeable
across solver APIs, but they establish that MOSEK did not receive the compact
quadratic-objective representation used by CLARABEL.

The annual MOSEK arm peaked at 9,287 MiB RSS, versus 5,963 MiB for the accepted
CLARABEL annual solve. MOSEK's presolve and factor setup were successful, but
the interior-point iterations stalled at iterations 54–56 and ended with
problem and solution status `UNKNOWN`. The final log still reported a primal
constraint violation of 1.0 and a dual variable violation of 0.1, so rejecting
the result was not merely a difference in status naming.

## Solver-specific interpretation

### CLARABEL

CLARABEL received the compact sparse quadratic cone problem without an
objective epigraph. This is an intended strength of the solver: the
[CLARABEL documentation][clarabel-home] states that it handles quadratic
objectives directly and avoids the epigraphical reformulation required by
standard homogeneous self-dual embedding approaches. Its default numerical
machinery includes equilibration, a direct KKT solve, static and dynamic KKT
regularization, and iterative refinement; see the
[CLARABEL settings reference][clarabel-settings].

That combination is a good match for this model: a sparse diagonal quadratic
objective, many affine equalities from nodal balance and storage recurrence,
simple bounds, and a long repeated time structure. This is a mechanistic
explanation consistent with the observations, not proof that any one CLARABEL
feature caused the speedup.

### MOSEK

The original hypothesis is substantially supported: CVXPY converted the
separable quadratic objective into millions of small second-order cones before
passing the problem to MOSEK. The annual size expansion and higher measured
memory are direct evidence that this route was materially more expensive for
this instance.

There is a second plausible representation issue. CVXPY had already dualized
its conic form, while the MOSEK log says that MOSEK chose to solve “the
primal”—the primal of the task it received, which is the CVXPY-dualized
orientation. CVXPY's own [MOSEK guidance][cvxpy-solver-docs] says that all
continuous problems are dualized by its MOSEK interface and specifically
recommends setting `MSK_IPAR_INTPNT_SOLVE_FORM=MSK_SOLVE_DUAL` when this path
is slow. MOSEK likewise documents that its primal/dual heuristic can select
the less efficient orientation and may be overridden; see
[MOSEK presolve and dualization][mosek-presolve].

The conclusion must remain qualified. MOSEK documents that conic
reformulations can be more robust and faster even when they contain more
variables and constraints, so model size alone is not a proof of the cause;
see [MOSEK's quadratic-optimization guidance][mosek-quadratic]. The observed
stagnation could reflect the combination of epigraph geometry, primal/dual
orientation, scaling, and this specific sparse structure. MOSEK documents
`UNKNOWN` as the outcome expected for a stall or numerical issue, rather than
an infeasibility certificate; see [MOSEK solution statuses][mosek-status].

### OSQP

OSQP received a compact native QP, so objective epigraph expansion does not
explain its result. OSQP uses an ADMM iteration and terminates when its primal
and dual residuals satisfy absolute-plus-relative thresholds; see the
[OSQP algorithm and termination criteria][osqp-algorithm]. CVXPY 1.9.2 sets
`eps_abs=eps_rel=1e-5` and `max_iter=10000` unless overridden in its
[OSQP interface][cvxpy-osqp-interface].

For this model, those general algebraic stopping tests did not imply the
stricter, unit-aware physical audit. The 24-hour arm stopped after 6,950
iterations with an unsuccessful polish, native primal residual 4.51e-3, and
native dual residual 1.25e-2. The 168- and 720-hour arms reached the 10,000
iteration limit, and all completed OSQP primals failed nodal-balance or storage
recurrence checks. The evidence therefore points to an algorithm/tolerance
match problem, not an incorrect QP representation.

### SCS

SCS received the same compact quadratic data as CLARABEL, so canonical-form
size does not explain the performance gap between those two solvers. SCS uses
Douglas–Rachford splitting on a homogeneous embedding of the quadratic cone
problem; see the [SCS algorithm][scs-algorithm]. First-order convergence was
much slower here: 13,375 iterations at 24 hours and 36,175 at 168 hours. Both
nominally solved prefixes failed the external physical audit, and the 720-hour
arm exceeded five minutes.

SCS emphasizes that scaling strongly affects first-order convergence and uses
heuristic data equilibration and adaptive scaling; see the
[SCS equilibration][scs-equilibration] and [settings][scs-settings]
documentation. The annual `unbounded` result cannot describe the frozen model
correctly because CLARABEL produced an independently audited feasible and
finite optimum for that same problem. It is therefore evidence of a numerical
or certificate failure on this representation, but the retained run does not
isolate its internal cause.

### HiGHS

HiGHS also received a compact native QP, with leaf bounds represented directly.
The tested version's default QP method is a primal active-set solver that uses
a dense Cholesky factorization of the reduced Hessian. The current
[HiGHS solver documentation][highs-solvers] describes that method and the
newer HiPO interior-point QP alternative.

The active-set path was accurate at 24 hours but required 6,655 QP iterations
plus 2,241 simplex iterations and took 11.31 seconds. At 168 hours it reached
iteration 8,000, its reported nullspace dimension changed from zero to one,
and it terminated as non-convex. Because the supplied Hessian is diagonal and
positive semidefinite, this does not establish actual model nonconvexity. It is
most consistent with a numerical reduced-Hessian classification in the
active-set path. The result suggests that this particular active-set method is
not well matched to the growing, bound-heavy time-vectorized QP; it does not
exclude the newer HiPO interior-point method.

## Scientific conclusion and follow-up

CLARABEL's result is best explained by alignment among representation,
algorithm, and numerical safeguards:

1. CVXPY preserved the quadratic objective rather than expanding it into an
   SOC epigraph.
2. CLARABEL used an interior-point method with direct sparse linear algebra and
   regularization on the equality-heavy repeated-time structure.
3. Its returned solutions satisfied the independent physical and objective
   audits at every horizon, including the annual problem.

The matrix supports retaining CLARABEL as the authoritative solver for the
frozen Case118 hierarchy. It does not show that the other solvers are incapable
of solving the model after representation changes or tuning.

The highest-value targeted follow-ups are:

1. rerun MOSEK at 24, 168, and 720 hours with
   `MSK_IPAR_INTPNT_SOLVE_FORM=MSK_SOLVE_DUAL` before considering another
   annual attempt;
2. test the HiGHS HiPO interior-point QP path, if supported by the installed
   build;
3. test tighter OSQP tolerances and a larger iteration budget, with any changes
   to scaling or `rho` predeclared; and
4. treat SCS tuning as lower priority given its 168-hour iteration growth and
   720-hour timeout.

These would be new, explicitly tuned experiments. They must not be folded into
the completed default-settings matrix or used to retroactively alter its
disposition.

## Reproducibility

The compact machine-readable record is
[M14C_SOLVER_MATRIX_RESULTS.json](M14C_SOLVER_MATRIX_RESULTS.json). It binds the
exact tracked runner and the ignored raw protocol, reference, supervision, arm,
and log chain through SHA-256 digests. The raw 248 KiB output remains under
`experiments/case118_annual_hierarchy/results/s4_solver_matrix_001`.

Relevant retained local artifacts include:

- [24-hour OSQP log][osqp-24-log];
- [24-hour SCS log][scs-24-log];
- [24-hour HiGHS log][highs-24-log];
- [168-hour HiGHS log][highs-168-log];
- [annual SCS log][scs-annual-log]; and
- [annual MOSEK log][mosek-annual-log].

The raw result directories are intentionally ignored experiment artifacts, so
their relative links resolve in a checkout that retains the completed study
but not in a fresh clone. The tracked compact record is the portable result.

This characterization does not alter solver authority, annual authority, or
the accepted annual outer trajectory. CLARABEL remains the frozen lossy-DC
solver for the Case118 hierarchy.

[clarabel-home]: https://clarabel.org/stable/
[clarabel-settings]: https://clarabel.org/stable/api_settings/
[cvxpy-solving-chain]: https://github.com/cvxpy/cvxpy/blob/v1.9.2/cvxpy/reductions/solvers/solving_chain.py#L211-L239
[cvxpy-clarabel-interface]: https://github.com/cvxpy/cvxpy/blob/v1.9.2/cvxpy/reductions/solvers/conic_solvers/clarabel_conif.py#L116-L120
[cvxpy-scs-interface]: https://github.com/cvxpy/cvxpy/blob/v1.9.2/cvxpy/reductions/solvers/conic_solvers/scs_conif.py#L133-L137
[cvxpy-mosek-interface]: https://github.com/cvxpy/cvxpy/blob/v1.9.2/cvxpy/reductions/solvers/conic_solvers/mosek_conif.py#L147-L198
[cvxpy-osqp-interface]: https://github.com/cvxpy/cvxpy/blob/v1.9.2/cvxpy/reductions/solvers/qp_solvers/osqp_qpif.py#L94-L114
[cvxpy-solver-docs]: https://www.cvxpy.org/tutorial/solvers/index.html#mosek-options
[mosek-presolve]: https://docs.mosek.com/latest/pythonapi/presolver.html
[mosek-quadratic]: https://docs.mosek.com/11.1/pythonapi/prob-def-quadratic.html
[mosek-status]: https://docs.mosek.com/latest/pythonapi/accessing-solution.html
[osqp-algorithm]: https://osqp.org/docs/solver/
[scs-algorithm]: https://www.cvxgrp.org/scs/algorithm/index.html
[scs-equilibration]: https://www.cvxgrp.org/scs/algorithm/equilibration.html
[scs-settings]: https://www.cvxgrp.org/scs/api/settings.html
[highs-solvers]: https://ergo-code.github.io/HiGHS/dev/solvers/
[osqp-24-log]: ../case118_annual_hierarchy/results/s4_solver_matrix_001/0024-osqp/worker.log
[scs-24-log]: ../case118_annual_hierarchy/results/s4_solver_matrix_001/0024-scs/worker.log
[highs-24-log]: ../case118_annual_hierarchy/results/s4_solver_matrix_001/0024-highs/worker.log
[highs-168-log]: ../case118_annual_hierarchy/results/s4_solver_matrix_001/0168-highs/worker.log
[scs-annual-log]: ../case118_annual_hierarchy/results/s4_solver_matrix_001/8760-scs/worker.log
[mosek-annual-log]: ../case118_annual_hierarchy/results/s4_annual_outer_rated_mosek_timing_003/worker.log
