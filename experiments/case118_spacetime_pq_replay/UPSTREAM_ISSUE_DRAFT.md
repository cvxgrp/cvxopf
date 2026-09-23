# Draft CVXPY issue

Suggested destination: https://github.com/cvxpy/cvxpy/issues

Title: DNLP/IPOPT convergence changes after 1.9.3 dense-constant sparse dispatch; matching derivative values at the initial point

We have a reproducible convergence sensitivity in a three-hour, 118-bus AC optimal
power flow model. Holding application code, inputs, initial point, native libraries,
and numerical solver options fixed, CVXPY 1.9.2/sparsediffpy 0.3.0 converges in
70 iterations, while 1.9.3/0.6.1 reaches IPOPT's 3,000-iteration limit.

With **1.9.3/0.6.1 unchanged**, setting
`cvxpy.settings.SPARSE_DENSITY_THRESHOLD = 0.0` in a fresh diagnostic process
restores the old Jacobian structure and convergence in 70 iterations. The accepted
solution matches every retained named variable and extracted result from the
historical successful run exactly. We are not claiming an incorrect derivative:
the tested values agree exactly, while the reported sparsity structure changes.

## Controlled results

| CVXPY / sparsediffpy | Density dispatch | IPOPT result | Iterations | Stored Jacobian entries |
|---|---|---|---:|---:|
| 1.9.2 / 0.3.0 | historical behavior | optimal, accepted | 70 | 412,439 |
| 1.9.3 / 0.6.1 | default | iteration limit, rejected | 3,000 | 36,167 |
| 1.9.3 / 0.6.1 | diagnostic threshold 0 | optimal, accepted | 70 | 412,439 |

After eliminating exact zeros at the starting point, all three Jacobians have
the same 35,451 nonzero entries and exactly equal values. The density-disabled
new-stack control also restores the exact old COO coordinate sequence and values.
All three Hessian structures have 10,104 entries.

The model uses **time vectorization only**, sparse P/Q variables, and per-entry
spatial P/Q equations. It has 9,124 native variables and 10,601 constraints.
No spatial P/Q batching is involved in this comparison.

We verified the entire ordered canonical expression fingerprints (atom types,
metadata, full constant/parameter values, and canonical variable positions),
constraint block ordering, and native bounds. The objective, all constraint values,
objective gradient, and coordinate-aligned Jacobian agree exactly at the same full
initial vector. Three Lagrangian Hessians also agree exactly, using objective factor
one and zero, all-one, and seeded random constraint multipliers. This is a bounded
starting-point check, not a global derivative proof or finite-difference test.

## Relevant source change

### Follow-up with the current application and all four vectorization modes

We repeated the frozen interval with 1.9.3/0.6.1, setting the density threshold to
zero in each fresh worker. All modes use sparse P/Q variables. Each full starting
point/layout exactly matches its corresponding earlier default-dispatch run;
core application source and numerical solver settings are unchanged.

| Time vectorization | Spatial P/Q batching | Default-dispatch iterations/result | Threshold-zero iterations/result | Threshold-zero IPOPT seconds |
|---|---|---|---|---:|
| off | off | 3,000 / rejected | 339 / accepted | 212.517 |
| on | off | 3,000 / rejected | 70 / accepted | 31.903 |
| off | on | 3,000 / rejected | 190 / accepted | 98.332 |
| on | on | 3,000 / rejected | 76 / accepted | 32.188 |

Time-only again exactly matches the historical accepted solution. All four have
close objectives (range 0.00781 around 266883.72), but reactive allocations differ;
we do not claim identical solutions across vectorization modes. This is one
observation per mode, not a general robustness or performance benchmark. The
initial-point derivative comparison above is for time-only, not all four modes.

### Installed package evidence

The installed `ipopt_nlpif.py` and `nlp_solver.py` files are byte-identical between
these CVXPY versions. In `diff_engine/converters.py`, 1.9.3 adds dispatch of dense
constant left-multiplication operands with nonzero density below 0.05 to the sparse
CSR binding. Related changes affect CSR array conversion and quadratic forms.
The process-local threshold control implicates this conversion behavior; we have
not isolated the subsequent mechanism inside IPOPT/MUMPS.

One plausible explanation is that removing structurally reported zeros changes
sparse factorization ordering and the nonlinear trajectory. We have not measured
factorization permutations, pivoting, or inertia, so that remains a hypothesis.
The initial derivative comparison provides no evidence of wrong derivative values
at that point. Later-iterate correctness has not been established by this test.

## Environment and settings

- macOS ARM64; Python 3.11.15.
- cyipopt 1.7.0, IPOPT 3.14.19, MUMPS 5.6.2.
- NumPy 2.4.6, SciPy 1.17.1, pandas 3.0.3, CLARABEL 0.11.1.
- Identical cyipopt extension and recursively linked Homebrew native binary hashes
  across the paired environments; same host and recorded thread environment.
- IPOPT: `mu_strategy=adaptive`, `tol=1e-7`, `bound_relax_factor=0`,
  `hessian_approximation=exact`, `derivative_test=none`,
  `least_square_init_duals=no`; `print_level=5`, `sb=yes` for retained logs.
  No iteration-limit override, tuning, helpers, or recovery.

New default-stack native termination values include unscaled constraint violation
`1.3264780494637305e-3` and dual infeasibility `3.8946565871375519e11`.
The attempt was rejected; its objective is not a feasible-cost comparison.

## Small standalone demonstration and full reproducer limits

Attached `reproduce_sparse_dispatch.py` demonstrates the structure change without
the application or an IPOPT solve: minimize a sum of squares with `I @ X == 1`,
where `I` is a dense 24-by-24 identity and `X` is 24-by-3. It verifies the exact
analytical Jacobian and gradient and prints the number of stored Jacobian entries.
Run with each compatible dependency pair, and on 1.9.3 also with `--dense-route`.

Expected stored entries: 1,728 on the old stack, 72 on the new default stack, and
1,728 on the new stack with the density control. The numerical Jacobian is the
same identity in every case. **This small example demonstrates sparsity behavior;
it is not a minimal reproduction of nonconvergence.**

The convergence result currently requires our retained Case118 model, frozen causal
start, and application harness. We have preserved the complete logs, starts,
source hashes, derivative arrays, and accepted/rejected results. Those artifacts
should be packaged and checked for portability before promising an independently
runnable full issue attachment; the local reproduction uses retained experiment
data and is not yet a standalone upstream bundle.

## Questions for maintainers

Is there a supported per-problem or per-solve way to preserve the dense derivative
representation for selected constant matrix products? We would prefer that over
relying on an internal global setting. Is this degree of IPOPT/MUMPS sensitivity
an expected tradeoff of the new dispatch, and what targeted evidence would be most
useful to distinguish ordering/pivoting sensitivity from a later-iterate issue?

This is a draft for review, not a submitted issue. CVXPY is the suggested initial
destination because its dispatch rule is the demonstrated trigger; no sparsediffpy
defect has been established.
