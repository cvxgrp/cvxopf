# Hour 6047: controlled dependency comparison

Completed 2026-09-22 after owner authorization of the solve harness and convergence
test, with the external fan confirmed on. Both attempts are complete; no solve
remains running. Compact evidence: `artifacts/provenance/convergence_summary.json`.

## Finding

Holding historical source code fixed, changing only the installed CVXPY and
sparsediffpy versions changes this window from successful convergence to a
3,000-iteration failure. The historical source reproduces both the old successful
result under the old packages and the recent failed time-only result under the
new packages exactly, including every retained named solution value and extracted
result field.

The dependency-pair transition is therefore sufficient to reproduce the convergence
regression in this controlled setup. Later cvxopf implementation changes are not
necessary for the observed failure. This does not identify which package/change
is responsible, establish an incorrect derivative, or distinguish a mathematical
defect from numerical sensitivity of equivalent reductions. It does not establish
infeasibility or a general failure across other windows.

## Results

**Both arms used time-only vectorization:** the three-hour AC model was vectorized
across time, while spatial P/Q entries used the historical per-entry construction
(no spatial P/Q batching). Sparse P/Q storage was enabled. Neither arm used the
fully stepwise (`none`) condition; vectorization was held fixed while only the
CVXPY/sparsediffpy dependency pair changed.

| Arm | CVXPY | sparsediffpy | Status / acceptance | Iterations | Native IPOPT seconds | Solve-phase seconds |
|---|---|---|---|---:|---:|---:|
| A | 1.9.2 | 0.3.0 | optimal / accepted | 70 | 29.572 | 31.561 |
| B | 1.9.3 | 0.6.1 | user_limit / rejected | 3,000 | 292.880 | 294.957 |

IPOPT's printed native times and iteration counts are used above. Solve-phase
time is the recorded interval from before canonicalization to solver return and
associated result handling; it is not pure native solve time. These are one attempt
per arm, not timing replicates or successful-solve speedup estimates.

Arm A's extracted objective is `266883.71728151327`, exactly matching the historical
successful replay. Arm B's extracted objective is `266909.3878353608`, while IPOPT's
final unscaled objective is `266912.1465500863`; these are distinct retained fields
from a rejected iterate and are not feasible-cost comparisons.

| Native termination quantity, unscaled | A | B |
|---|---:|---:|
| Constraint violation | 5.1099e-8 | 1.3265e-3 |
| Dual infeasibility | 1.9776e-4 | 3.8947e11 |
| Complementarity | 2.4358e-5 | 9.0167e5 |

Keep these native quantities separate from the existing extracted physical audit.
The latter reports much smaller lifted nodal-balance residuals in B, but its status
gate correctly rejects the attempt. As explained in the handoff, those lifted
balance checks do not independently establish nonlinear network feasibility.

## Controls and execution

- Historical source snapshot anchored at `2ac05b039`, with all 105 recorded source
  hashes verified before and after execution. The owner checkout remains on
  `todo-fix`; no source snapshot file was edited.
- Python 3.11.15, identical 49-package inventories except the two specified versions,
  identical cyipopt extension and nine linked Homebrew binary hashes, identical
  recorded thread environment, and the same host.
- Identical frozen request, policy, solver configuration, causal start, named
  physical coordinates, all 9,124 canonical x0 coordinates, normalized layout,
  and native bounds. Both models have 10,601 native constraints.
- Original historical preparation, execution, start/result persistence, and
  post-return acceptance audit. Retained data paths are adapted explicitly; model
  and initializer code are unchanged.
- Sequential fresh workers: A once, then B only after A's successful acceptance.
  No helper, recovery, retry, warm-start substitution, or numerical tuning.
- The only forwarded option change is `print_level=5` for diagnostic logs. No
  iteration-limit override was added. B reached IPOPT's default 3,000 iterations.
- Both final harness paths passed mocked native-entry checks before launch, and
  Ruff passed. Harnesses, preparation evidence, expected starts, and the supervisor
  are bound by SHA-256 in `solve-binding.json`; all bound files still matched at
  completion. This run binds reviewed-in-session file contents rather than claiming
  an owner commit of the new harnesses. The changes remain unstaged.

The supervisor confirmed process monitoring and collected a thermal sample before
admitting A. It retained process observations and 35 machine-wide temperature
samples; observed average CPU sensor temperature ranged from 44.70 to 66.90 °C.
The fan setting was confirmed by the owner. These observations do not prove that
thermal conditions or throttling were identical throughout the test.

Historical native binary hashes were not retained in the original experiment, so
they cannot be retrospectively verified. Exact reproduction of A's stored solution
is stronger empirical evidence than package version agreement alone, but does not
reconstruct missing binary provenance.

## Retained evidence and next decision

Raw evidence lives under
`results/hour6047_environment_reproduction/convergence-test/`: separate A/B logs,
launch records, starts, results, native options/results, phase records, acceptance
summaries, process records, and thermal samples. The sibling `solve-binding.json`
identifies the executed harness files and frozen preparation evidence.

`solve_historical.py` runs one verified primary; `run_historical_pair.py` enforces
the sequential acceptance gate. `prepare_historical.py` remains preparation-only.
No additional experiment was launched after B failed.

The next proposed investigation is to localize the change inside the compatible
CVXPY/sparsediffpy transition, using package-source differences and, if warranted,
bounded oracle comparisons at shared physical points. Installed metadata requires
sparsediffpy `>=0.3.0,<0.4.0` with CVXPY 1.9.2 and `>=0.6.0,<0.7.0` with 1.9.3;
an unsupported mixed-version pair would not be a valid ordinary compatibility
control. Any new numerical experiment should have its own narrow design and
authorization.
