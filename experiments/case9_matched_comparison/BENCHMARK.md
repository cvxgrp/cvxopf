# Matched Case9 AC benchmark

The benchmark runs one CVXOPF/IPOPT arm and one PYPOWER/PIPS arm for each of the
four [preflight cases](PROTOCOL.md). It does not change the model or the
historical results. Only one worker is active at a time, with one numerical
thread. Solver order is reversed in two cells so each solver runs first once
at each cost level and once at each link level.

## Settings to approve before launch

The [settings file](BENCHMARK_SETTINGS.json) is a **proposal**, not launch
authority. It uses the stock CVXOPF DNLP/IPOPT path with 3,000 iterations,
PIPS with 150 iterations, a 300-second whole-worker ceiling, and a 16 GiB
sampled worker RSS ceiling. The proposed material difference between accepted
optimized costs is 1 objective unit. The solver stopping tolerances and the
original-unit audit tolerances are different controls. Review both.

Both solvers start from the same frozen engineering-unit point in each cell:
the preflight's `accounting` probe, not a previously solved point. CVXOPF
reconstructs its lifted leaves, then records the stock IPOPT adapter's actual
`x0` and layout. PYPOWER reconstructs its dummy generators and PWL epigraph
and records the exact vector passed to PIPS. Neither solver borrows the other's
iterate, changes slack generation during audit, or retries an unsuccessful
solve. Native solvers may perform their own bound interiorization.

## Bind, inspect, launch

From the repository root, use the project environment plus a separate Python
interpreter pinned to PYPOWER 5.1.19, NumPy 2.2.6, and SciPy 1.18.0. Set
`PYP_PYTHON` to the absolute path of that interpreter. Commit the runner
and run a fresh solver-free preflight against the same source:

```sh
uv run --offline --no-sync --extra dev python \
  -m experiments.case9_matched_comparison.preflight \
  --run-id benchmark_preflight_001 --pypower-python "$PYP_PYTHON"

uv run --offline --no-sync --extra dev python \
  -m experiments.case9_matched_comparison.benchmark bind benchmark_001 \
  --preflight benchmark_preflight_001 --pypower-python "$PYP_PYTHON" \
  --settings experiments/case9_matched_comparison/BENCHMARK_SETTINGS.json
```

Binding creates no solver processes and leaves the worktree unchanged. The
command prints the SHA-256 of the new immutable `binding.json` under
`outputs/benchmark_001/`. Inspect the binding and get separate approval for
that configuration before passing its digest to `launch`. Launch records the
matching authority under the same ignored directory.

```sh
uv run --offline --no-sync --extra dev python \
  -m experiments.case9_matched_comparison.benchmark launch benchmark_001 \
  --binding-sha256 BINDING_SHA256

uv run --offline --no-sync --extra dev python \
  -m experiments.case9_matched_comparison.benchmark status benchmark_001
```

## Stop and resume

Create an empty `STOP` file inside the run directory for a controlled stop,
or press Ctrl-C in the supervisor. The supervisor stops and reaps its child.
A stop during a short cross-audit is observed between audit workers; each audit
process has its own 30-second deadline. An arm that launched is never retried.
An interrupted, failed, or timed-out solve remains in the matrix as such.
Resume can repeat an unlaunched reservation or an incomplete **audit**, but
will not repeat a numerical solve. A timeout advances to the next arm; an RSS
limit, lost worker, or failed audit process stops launches for inspection.
After an abrupt supervisor loss, resume refuses a live worker and charges the
whole ceiling for a launched attempt without finalized timing. If a complete,
hash-checked native return survives, resume audits it for diagnostics without
repeating the solve or admitting it to a paired verdict.

```sh
uv run --offline --no-sync --extra dev python \
  -m experiments.case9_matched_comparison.benchmark resume benchmark_001 \
  --binding-sha256 BINDING_SHA256 --acknowledge-stop
```

## Read the results

Every finalized return preserves the native vector, objective, status, solver
diagnostics, start, and effective options. The source worker evaluates that
vector, including its auxiliary variables, before either preflight evaluator
reconstructs the dispatch. IPOPT's native objective (with its adapter offset)
and CVXPY's public objective are recorded separately. Each usable physical
return is evaluated in both model builds, even for a nonconverged solver.
A result is accepted only if supervision completed, the source solver
converged, and the native vector, both models, and the independent cost and
converter checks pass. IPOPT's native bound and row checks use its configured
constraint-violation limit; PIPS's native checks use the original-unit gates.
The pair report withholds a cost ranking and
aggregate losses when either arm is not accepted. Any cheaper cross-feasible
result is an observed value, not a global optimum.

Use supervised whole-worker wall time for the end-to-end timing comparison;
it includes setup, the solver call, and raw archival for both solvers, but not
the separate audit workers. The local per-side call timers are diagnostic:
the public CVXOPF call includes DNLP canonicalization, whereas the PIPS call
begins after its OPF assembly. Neither timing isolates the solver algorithm.
Logs, sampled RSS, attempts, and audit receipts stay under ignored `outputs/`.
