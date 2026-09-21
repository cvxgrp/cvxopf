# Hour 6047: isolated primary comparison

The combined-vectorization study stopped when hour 6047 exhausted recovery.
The owner requested one primary solve with and one without time vectorization.
This diagnostic is separate from the frozen 120+6 study; it does not resume,
replace, or amend any of that study's attempts.

Run exactly two fresh processes sequentially: `stepwise`, then `vectorized`.
Both use spatial P/Q vectorization and sparse storage, which are the current
library behavior. There is no spatial-vectorization switch. No helpers,
perturbations, recovery replays, or further timing conditions are admitted.

Use hour 6047's retained primary request and preceding-controller start, including
the same three-hour horizon, initial/terminal SoC, physical inputs, objective,
acceptance policy, and numerical solver options. Verify each named starting
coordinate exactly, unpacking the time-vectorized representation for comparison.
Keep CVXPY 1.9.3, sparsediffpy 0.6.1, and the other failed-study dependencies.
Verify the failed study's execution source hashes, and bind the diagnostic code
and this plan too. Work in the owner's existing checkout and branch.

The only solver-option override is `print_level=5`, to retain IPOPT iteration
counts, infeasibilities, and termination reasons in each `worker.log`. Record
the unchanged frozen options and the solver-boundary logging override explicitly.
A process-local scoped IPOPT subclass forwards the original solver call with
only its print level changed, after the existing frozen-configuration validation
and start capture. The retained physical request hash describes the original
numerical request, not this logging override. No numerical
tuning, new time limit, or tolerance relaxation is permitted.

Before execution, obtain clean scientific review from `cvxopf-review` and the
owner's review and commit. Preparation is allowed before that gate and does
not call the solver:

```sh
uv run --locked --extra dev python -m experiments.case118_spacetime_pq_replay.diagnose_primary \
  --prepare-only --output experiments/case118_spacetime_pq_replay/results/case118_6047_primary_preparation
```

After the owner commits, bind the full commit SHA to execute the pair:

```sh
uv run --locked --extra dev python -m experiments.case118_spacetime_pq_replay.diagnose_primary \
  --commit <full-reviewed-commit-sha> --fan-on \
  --output experiments/case118_spacetime_pq_replay/results/case118_6047_primary_diagnostic_retry
```

Use process-monitoring and thermal-telemetry permissions from the outset. The
external fan stays on. Check that no other experiment solves are active before
launch. The runner checks `ps` access before creating the output, starts thermal
collection before solving, and records unavailable telemetry explicitly. Output
directories must be new and separate from both retained studies. No automatic
retry is permitted after interruption or failure.

Retain per-mode preparation proof, phase times, full solver log, start packet,
result with physical residual audit, process return code, elapsed time, and
configuration. A rejected solver result does not prevent executing the other
condition; a Python/process failure stops the pair and preserves evidence.
`finished.json` means both processes completed, not that either solve succeeded.

Compare solver status, iterations, residuals, objective, and solve-phase time
(`before_ac_solve` to `after_ac_solve`, including canonicalization and start
persistence). Preparation/build time includes rebuilding the historical start,
so do not interpret it as pure model construction. Interpret two isolated solves
as a diagnostic, not a replicated timing estimate. This comparison tests temporal
representation with spatial vectorization enabled in both conditions; it cannot
isolate spatial vectorization or dependency changes. Keep the failed study's
original primary and prior historical results as separate reference evidence.

The first launch at commit `2a3dde18448fd0054e7601a1ed50512ec0c086c8` is
preserved in `experiments/case118_spacetime_pq_replay/results/case118_6047_primary_diagnostic/`. It stopped on the
stepwise primary before IPOPT entry because the initial implementation put the
logging option into the hash-locked solver configuration. It ran zero native
solves and never launched the vectorized condition. The corrected diagnostic
requires another owner-reviewed commit and explicit retry authorization; use
the separate unused output directory above and retain the failed launch intact.
