# Milestone 9 — Sparse P/Q variables for AC-OPF

Controlled by `OPFOptions.sparse_pq` (default `True`).

When `sparse_pq=True`, `P` and `Q` are declared as flat `(nnz,)` CVXPY
variables `P_vec` and `Q_vec` over the Ybus sparsity pattern rather than
dense `(nb, nb)` matrices. This eliminates `2*(nb²-nnz)` trivially-zero
variables and the `P[Z]==0` / `Q[Z]==0` equality constraints that exist
only to compensate for the dense declaration. For case118, this reduces
P+Q variable count from ~27,848 to ~594.

Nodal injections use a precomputed `(nb, nnz)` scatter matrix `Rp` (stored
in `build.data`) such that `p = Rp @ P_vec` and `q = Rp @ Q_vec`.

When `sparse_pq=False`, the legacy dense formulation is used unchanged.
This path is preserved for research comparison and timing benchmarks;
`notebooks/benchmark_opf.py` times both paths across all test cases.

Files changed: `ac_problem.py`, `problem.py` (`OPFOptions`),
`tests/test_problem_single.py`, `tests/test_problem_multistep.py`,
new `tests/test_sparse_pq.py`, new `examples/case9_sparse_vs_dense_ac.py`,
updated `notebooks/benchmark_opf.py`.

Do not set `sparse_pq=True` and then access `build.variables["P"]` —
the key will not exist. Use `build.variables.get("P_vec")` or check
`build.variables` keys when writing formulation-agnostic code.
