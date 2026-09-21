# M4 Stage 0 — AC branch-limit characterization

This folder contains the disposable characterization harness for Stage 0 of
[`plans/milestone-4-branch-limits.md`](../../plans/milestone-4-branch-limits.md).
It tests the proposed branch-terminal expression structure without changing
the production builders.

The experiment answers four questions:

1. Do scalar direct terminal-flow expressions agree with independent complex
   arithmetic at a fixed voltage state?
2. Can CVXPY DNLP solve the direct normalized thermal constraints reliably,
   and how does that compare with lifted terminal-flow variables?
3. What construction and solve overhead comes from unused direct expressions,
   the actual lifted reporting-only structure, and lifted enforcement?
4. Do positive `sparsity_tol` values provide useful savings, and what physical
   inconsistency do they introduce?

Run the complete initial characterization from the repository root:

```bash
uv run python experiments/branch_limits_s0/s0_characterization.py
```

The script writes `results/s0_results.json`. The running interpretation is
recorded append-only in `experiment_log.md`. The reviewed conclusions are in
[`S0_REPORT.md`](S0_REPORT.md).

Production modules are intentionally not imported for branch-terminal
admittance or flow construction. The experiment contains a local candidate
implementation so Stage 0 can reject or refine the approach without creating
an accidental production contract.

Stage 0 found that direct voltage expressions are numerically correct but
perform poorly when composed directly inside thermal inequalities. The
approved production path retains those expressions as the authoritative
equations and ties them to lifted terminal-flow variables used identically for
reporting and enforcement.
