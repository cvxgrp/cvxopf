# Hour 6047: four-way vectorization diagnostic

The owner requested a fresh repeat of all four combinations, including the
conditions already measured. Run exactly one isolated primary attempt per
condition for the same three-hour window starting at hour 6047:

| Order / directory | Time assembly | Spatial P/Q batching |
|---|---|---|
| 1. `none` | stepwise | off |
| 2. `time_only` | vectorized | off |
| 3. `spatial_only` | stepwise | on |
| 4. `both` | vectorized | on |

These labels refer to the two selected assembly controls; other existing
operations, including branch-terminal constraints, remain unchanged. All four
use sparse P/Q storage. Each condition runs in a fresh process, sequentially,
with no helpers, perturbations, recovery, or feedback from earlier results.
An ordinary solver rejection still permits the next condition. A process or
structured Python error stops the diagnostic and preserves all evidence.

Use the same frozen primary request, preceding-controller start, initial and
terminal SoC, load/renewables/device data, objective, and acceptance policy as
the earlier 6047 diagnostics. Reconstruct the original start with the frozen
inputs, then rebuild only the chosen temporal/spatial representation. Verify
every assigned physical coordinate exactly against the historical start after
unpacking the temporal representation. The original request hash identifies
the common retained request; each preparation additionally records its actual
representation settings and representation-specific input fingerprint. Do not
change the archived outer plan or overwrite its fingerprint to bypass validation.

Use current dependencies unchanged: CVXPY 1.9.3 and sparsediffpy 0.6.1, with
the other versions matched to the failed study. Explicitly permit only the
three execution-source changes from spatial-switch commit
`e432b8194c14645eae561d013e0eb052959dac36`: `src/cvxopf/problem.py`,
`src/cvxopf/ac_problem.py`, and
`experiments/case118_annual_hierarchy/streaming_runner.py`. Verify those file
contents against that commit and record old/new hashes; all other historical
execution sources must still match. Bind the four-way runner and this plan to
their current hashes, and require the owner's reviewed, clean commit at launch.

Retain the original numerical solver configuration and 3,000-iteration default
limit. The scoped solver-boundary adapter changes only `print_level=5` for
iteration logs, after frozen-config validation and exact canonical-start capture.
Do not relax tolerances, add timeouts, change solver algorithms, or substitute
historical successful solutions as new starts.

Prepare and review before execution. Preparation constructs models without
calling IPOPT:

```sh
uv run --locked --extra dev python -m experiments.case118_spacetime_pq_replay.diagnose_primary \
  --four-way --prepare-only --output outputs/case118_6047_four_way_preparation
```

Check all four real models through canonicalization and verified x0 capture
to a mocked native solver entry as well: no native solve during that check.
Get a clean scientific review from `cvxopf-review`, then the owner's review
and commit. Once committed and authorized, bind that exact commit:

```sh
uv run --locked --extra dev python -m experiments.case118_spacetime_pq_replay.diagnose_primary \
  --four-way --commit <full-reviewed-commit-sha> --fan-on \
  --output outputs/case118_6047_four_way
```

Check process-monitoring and thermal permissions before launch, confirm no
other experiment solves are active, and keep the external fan on. Record thermal
telemetry from before the first solve. Use a new output directory; preserve the
120+6 studies, failed diagnostic startup, and completed two-way diagnostic.
No automatic retry or additional condition is authorized by this protocol.

Report per-condition status, acceptance, iterations, native IPOPT time,
solve-phase time (canonicalization through start persistence and solver return),
physical residuals, and native primal/dual residuals. Treat native termination
metrics and extracted physical audits as distinct evidence; previous failed
attempts showed discrepancies. Rejected objectives are not feasible-cost
comparisons. Distinguish preparation overhead from pure build time, and total
wall time from solve time. Compare the four fresh conditions together, with
earlier runs clearly labeled as historical references. One attempt per condition
is a diagnostic, not a replicated timing estimate or proof of root cause.
