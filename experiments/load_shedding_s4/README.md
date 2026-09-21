# M19 Stage 4 shedding-bound representation spike

This experiment compares the two approved CVXPY representations of

$$
0 \leq \alpha_{t,i} \leq \rho_i m_{t,i}.
$$

The production candidate uses two explicit inequalities. The comparison uses
CVXPY leaf bounds $0\leq\alpha\leq\rho$ plus the always-present eligibility
inequality $\alpha\leq\rho m$. Both models use the same load parameters,
network equations, objective, and initial conditions.

Run from the repository root:

```bash
uv run python experiments/load_shedding_s4/compare_bound_representations.py
```

The script writes `results.json`. See [S4_REPORT.md](S4_REPORT.md) for the
recorded results and representation decision.
