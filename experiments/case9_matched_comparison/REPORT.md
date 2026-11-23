# Matched Case9 AC benchmark results

The previously reported CVXOPF cost advantage did not reproduce on these four
matched Case9 cases. All eight solves passed their native checks, and each
returned dispatch passed feasibility checks in both models without changing
slack generation. The largest absolute objective difference was **0.000929
cost units**, below the declared **1-unit** threshold. The largest absolute
difference in any generator's real-power dispatch was **0.014 MW**.

| Generator costs | DC links | CVXOPF objective | PYPOWER objective | CVXOPF − PYPOWER | Max. generator difference (MW) | Both dispatches feasible in both models? |
|---|---|---:|---:|---:|---:|---|
| Smooth | Off | 5345.290636 | 5345.290868 | −0.000232 | 0.000036 | Yes |
| Smooth | On | 5314.274908 | 5314.275837 | −0.000929 | 0.014043 | Yes |
| Piecewise-linear | Off | 6621.737591 | 6621.737831 | −0.000241 | 0.000022 | Yes |
| Piecewise-linear | On | 6249.865509 | 6249.865893 | −0.000384 | 0.000038 | Yes |

Objectives are the independently evaluated generator costs over one hour.
Differences are calculated before rounding. See the [preflight protocol](PROTOCOL.md)
for model parity and tolerances, including the separate 1 MW converter
withdrawal at bus 4 for the link-enabled cases. Agreement here is evidence
about the observed solutions, not proof of global optimality.

## Reconstruct the table

Run from the repository root in the project environment, with the retained
archive at `experiments/case9_matched_comparison/outputs/benchmark_001/`:

```sh
python -m experiments.case9_matched_comparison.benchmark status benchmark_001
```

This command checks the retained audit records without solving. The `pairs`
output supplies the signed objective difference and maximum generator difference.
For the two objective columns, read `verdict.common_objective` from each
accepted arm's `audit.json` at the attempt path listed under `arms`.
The archive was produced at commit `58db1e65c`; the raw files are not tracked.
