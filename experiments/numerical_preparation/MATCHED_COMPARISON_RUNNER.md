# Serial matched comparison runner

Implementation of [the reviewed design](MATCHED_COMPARISON_PLAN.md). No OPF
execution is authorized by this document or by passing the rehearsal tests.
Finalize E4 evidence, review/commit this implementation, and obtain approval of
the binding, budgets and launch command before numerical execution.

## Scientific configuration

Exactly 16 primary arms: two retained E3 energy-neutral fixtures, T=3 and T=24,
AC and SOCP, baseline and prepared. Each horizon has its own per-battery
50%-to-50% equality. The three-hour cases are not prefixes of a joint 24-hour
solution. Fixture offsets, policies, stock solver settings, physical gates and
device identities come from the reviewed plan and existing E3 helpers.

Execution is **serial**, in fresh single-numerical-thread processes. Adjacent
pairs alternate baseline-first and prepared-first. No races, recovery ladder,
solution-derived start, hourly fallback, solver retuning or numerical retry.
Both AC treatments use the same deterministic physical cold-start recipe;
preflight verifies physical inputs, settings and identity-matched starts.

AC accounting independently reconstructs the native canonical objective and
physical components for both representations. The baseline analysis reduction
tracks only the declared storage cycling abs terms; its native solver uses the
ordinary installed DNLP reduction. Baseline slack in MW is converted to cost
using each device's weight and delta before aggregation. Prepared slack is
already in cost coordinates. The same signed-excess subtraction,
non-cancelling cycling warning and unexplained-total gate apply to both.
No other auxiliary is relabeled cycling. Native full convergence and existing
physical/component gates remain mandatory. SOCP acceptance remains lifted-model
feasibility, not AC realizability or a certified global bound.

The installed DNLP dispatcher does not accept an observer solver instance. The
baseline worker therefore instruments only its own Problem's solve method,
through OPFBuild.solve, with the same stock reduction chain and a pass-through
IPOPT observer. It restores that instance method afterward; it never patches a
global solver method/registry or changes an objective, constraint or derivative.

## Timing definitions

All workers record the same disjoint monotonic phases:

| Clock | Meaning |
| --- | --- |
| slice_admission | Import-independent worker admission and materializing the frozen input slice |
| build | Public model construction |
| prospective_preparation | Complete cold physical/canonical start and maps for AC; prospective canonical assembly for SOCP |
| public_solve | OPFBuild.solve call, including actual canonicalization, preparation, native call and inversion |
| extraction | Physical publication and native/preparation evidence capture |
| independent_audit | Fresh inverse-chain replay, physical checks and economics |
| archive | Result serialization/compression, up to timing publication |
| worker_body_seconds | Worker entry through result/timing publication; excludes imports and final completion-manifest write |
| supervised worker_seconds | Parent launch through joined exit, including imports, polling and cleanup |

`native_call_seconds` is a **subset**, not another additive phase. AC records
the stock IPOPT adapter-call interval; SOCP retains CLARABEL's own solve timer.
Prepared AC constructs derivative oracles outside its adapter timer, whereas
baseline constructs them inside. Native timers therefore do not support an AC
speedup ratio. Matched runtime ratios use `public_solve` and whole-worker clocks
only, and only for pairs independently accepted on both sides. Timers in
different formulations are not interchangeable. Fake-worker rehearsal timings
are not numerical-performance estimates.

## Persistence, scope and resume

Default output is the new ignored `results/matched_comparison_001/`. The runner
refuses qualification, three-step, E3/E4 or other historical output directories.
Stable annual/input history is admitted once when creating a binding. Workers
materialize only their frozen slice and verify its complete round-trip identity;
they do not read/audit the annual inventory again. Dependency content/runtime
and input fingerprints are checked at invocation admission; source content is
also checked at each launch and worker entry. The numerical environment must
remain unchanged during an invocation.

`matched_model.context()` declares a source scope (production model plus listed
helper modules), numerical-distribution Python/binary hashes, actual input
hashes and effective thread/environment settings. Relevant dependencies must be
committed; unrelated dirty documents or a new HEAD with identical scoped content
do not prevent resume. Changes to relevant content/settings require a separate
compatibility decision, not a silently adopted binding. Historical archives are
read-only; no migration or rewritten acceptance labels.

The immutable attempt journal is authoritative. Each attempt retains its
request, launch, phases, resource samples, worker log, prospective start, native
return/preparation, result, timing, completion hashes and finalized supervision.
The full native/preparation payload is retained in the result as well as its
early-return archive. A complete bundle and successful supervision are required
before acceptance or advancement. Publication interrupted before updating
`progress.json` is recovered exactly once, without solving again. Fast status
checks hashes and worker independent-audit receipts; `--status --audit` performs
a fresh independent replay with optimization forbidden.

Numerical rejection, audit rejection and whole-worker timeout dispose only that
arm and leave its partner eligible to run. Infrastructure/RSS faults or operator
stop halt the study for review. Missing required process/RSS monitoring is fatal;
optional temperature, power and host-load telemetry are bounded and advisory.

Ordinary explicitly authorized resume **disposes an unfinished/failed attempt**
as unaccepted and proceeds to the next arm. It does not silently retry it. To
permit an interruption replacement, include `--replacement-allowance N` in the
reviewed initial launch and use `--retry-interrupted` explicitly on resume. The
default allowance is zero. All earlier effort and attempt reservations remain
charged; replacement candidates retain separate identities. Numerical timeouts
and completed rejected arms are never retried. A journal gap after parent loss
is charged the full reserved ceiling with uncertainty labeled; potentially live
orphan PIDs require operator inspection, never automatic termination/adoption.

Owner-approved budgets (2026-10-13) are **3,600 s per whole worker** at both T=3
and T=24, **16,384 MiB sampled RSS**, and **57,600 cumulative supervised
worker-seconds (16 hours)**, with one-second polling. These replace the original
unexecuted 300-/600-second and 7,200-second proposal, not any E4 budget. Solver
iteration limits and all numerical/physical/acceptance settings are unchanged.
Final admission is clipped to remaining time. Cleanup overrun remains charged.
Reserving replacements does not enlarge the cumulative time budget.
Matrix completion means every arm is disposed, **not** that all were accepted.

## Commands

From the repository root, using the normal installed environment. Preflight and
status never invoke optimizers. Preflight prints a compact summary and creates
no execution directory or authority.

```sh
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --offline --no-sync --extra dev \
  python -m experiments.numerical_preparation.run_matched --preflight
```

Only after owner commit and separate launch approval (replace FULL_COMMIT):

```sh
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --offline --no-sync --extra dev \
  python -m experiments.numerical_preparation.run_matched \
  --authorize-execution --commit FULL_COMMIT
```

Check progress quickly or independently replay the complete retained evidence:

```sh
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --offline --no-sync --extra dev \
  python -m experiments.numerical_preparation.run_matched --status
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --offline --no-sync --extra dev \
  python -m experiments.numerical_preparation.run_matched --status --audit
```

Request a stop (the parent terminates/joins its one owned worker), then resume
only after reviewing the stop and obtaining continuation approval:

```sh
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --offline --no-sync --extra dev \
  python -m experiments.numerical_preparation.run_matched --stop
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --offline --no-sync --extra dev \
  python -m experiments.numerical_preparation.run_matched \
  --authorize-execution --commit FULL_COMMIT --resume --acknowledge-stop
```

`--output experiments/numerical_preparation/results/matched_comparison_NAME`
selects another new run. A second supervisor cannot own the same run. Resuming
a complete study is a no-op. `--report` writes a fresh named `reports/` bundle
containing an all-arm JSON table and paired timing/cost figure, without changing
numerical evidence; add `--audit` for an independently replayed report.

```sh
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --offline --no-sync --extra notebook \
  python -m experiments.numerical_preparation.run_matched --report --audit
```

Missing/failed arms remain in the table. Timeouts are censored whole-worker
observations, not measured completion times or infeasibility certificates.
Physical costs remain distinguishable from native canonical costs, ENS and
explained cycling slack. Results are a bounded case study, not a distributional
reliability estimate, universal speedup or decomposition of individual effects.

## Non-solving verification

```sh
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --offline --no-sync --extra dev \
  pytest tests/test_matched_preparation.py tests/test_preparation_qualification.py \
  tests/test_tracy_e3_prepared.py -q
```

Tests forbid native optimizer construction. Analytic AC and SOCP returns exercise
all four real public-solve, worker-publication and full replay paths.
Synthetic workers exercise the real serial supervisor/CLI/journal in separate
fresh processes across two stop/resumes, preserve earlier result hashes and
carry budgets. Additional checks cover publication before checkpoint, partial
publication, worker/launch failure, declared replacements, source changes,
optional telemetry failure and required RSS failure. These are lifecycle and
analytic evidence, not new Tracy solve results. Full Tracy preflight constructs
all 16 graphs and eight complete AC starts with native optimization forbidden.

Observed implementation verification, 2026-10-13: **194 focused non-solving
tests passed**, including 30 matched-runner checks. The fresh-process CLI
rehearsal stopped and resumed twice, retained the first publication hash and
carried cumulative effort. Full Tracy CLI preflight passed with zero optimizer
calls and no execution directory created. Actual process/RSS access and bounded
advisory host observations were checked with host permission; sandboxed `ps`
without that permission was correctly unavailable. No numerical timing study,
native OPF solve, execution binding/authority, commit or launch was performed.
