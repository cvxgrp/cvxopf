# Running and restarting the Tracy AC comparison

This is the 16-trajectory, 96-action Stage D comparison, **not annual AC**.
The study has run and is currently stopped with 48 accepted actions. The
successive audit-only continuation support requires review and commit before
the next launch. This guide does not itself authorize execution.

## Before starting

Review `STAGE_D_PROTOCOL.md`, the selected periods and the implementation.
Commit the reviewed files and use that full commit ID below. The runner refuses
a dirty tree or a different commit. It verifies the original owner inputs,
accepted annual archives, selected starts and DC targets before creating output.
The ignored owner CSV and annual results must remain available; there is no
substitute-data mode.

Check that `ps` can read RSS under the permissions used for this terminal.
Record the machine/cooling configuration and check any intended temperature
logger before launch. Run with the laptop powered and cooled consistently.
Do not edit tracked source files while a source-bound run is active.

From the repository root, after explicit launch approval:

```bash
uv run --extra dev python -m experiments.case118_tracy_2021.run_stage_d run --commit FULL_COMMIT_ID
```

The default output is `experiments/case118_tracy_2021/results/stage_d`.
`run` requires a new output directory; it never overwrites an earlier run.
Use `--output PATH` consistently on every command for a different directory.

One fresh solver worker runs at a time. Library thread counts are fixed to one
before numerical imports. The only external resource cutoff is sampled worker
RSS above 16 GiB. There is no wall-time, study-time or automatic-stall cutoff;
IPOPT retains its default iteration limit. A numerical failure moves through
the finite recovery sequence. Exhaustion ends that trajectory and continues the
other independent trajectories. Resource or implementation failures stop the study.

After the cold/shifted primary, recovery tries three causal perturbations, a
flat hard-target start, then the target-free/copied pair and three perturbations
of the accepted target-free solution. This is at most ten calls per action;
missing target-free sources skip their dependent calls. The flat fallback does
not replace the original causal source used by the target-free diagnostic.

## Watch, stop and resume

```bash
uv run --extra dev python -m experiments.case118_tracy_2021.run_stage_d status
uv run --extra dev python -m experiments.case118_tracy_2021.run_stage_d stop
```

`status` summarizes the last reconstructed progress and latest worker phase;
it is not a live process inspection. Native IPOPT iterations are in the active
attempt's `worker.log`. A long solve is not automatically declared stalled.

`stop` requests cancellation; wait for the supervisor to confirm it has stopped
and reaped its child. Ctrl-C or SIGTERM to the supervisor also retains a stopped
record and terminates/reaps its worker. Never start a second supervisor on the
same output. A file lock prevents concurrent ordinary invocations.

After a deliberate stop or reboot, on the **same clean execution commit and
numerical environment**:

```bash
uv run --extra dev python -m experiments.case118_tracy_2021.run_stage_d resume --commit FULL_COMMIT_ID
```

Resume reconstructs the attempt journal, then prints the next trajectory,
global hour, look-ahead and recovery role before launching. Accepted actions and
completed trajectories are not rerun. The next hour uses its trajectory's own
realized state and accepted prediction, not nominal DC state or another horizon.
The interrupted solve starts again; internal IPOPT iterations are not resumed.
Previously accepted target-free sources remain available to their dependent
recovery attempts. Every retry has a new directory and retained timing record.

If an unfinalized invocation still has a possibly live child PID, resume refuses
to launch more work. Inspect that process and the retained attempt before
proceeding. After an abrupt interruption, timing can be incomplete and is
reported as a lower bound. An RSS or implementation failure is not automatically
retried: review the reason before changing the execution procedure or source.

### Audit-only source changes and restart preflight

The original run is bound to `f7aec7c562705749956adadd353216c780621a98`.
Its first continuation is bound to `cdc63822c5b87a684e419f1b3757b07e7b82b360`.
After owner review and commit of a correction, first check the exact restart
command without launching workers or writing study records:

```bash
uv run --extra dev python -m experiments.case118_tracy_2021.run_stage_d preflight \
  --commit NEW_REVIEWED_FULL_COMMIT_ID \
  --continue-from cdc63822c5b87a684e419f1b3757b07e7b82b360
```

For the currently stopped study, expect 48 accepted actions and the next request
at global hour 8581, W=6, role `causal_1`, with no state advancement for the failed
primary. Once preflight passes and restart is authorized, replace `preflight`
with `resume` using the same arguments. The 16 GiB ceiling and native IPOPT
iteration limit remain unchanged. Check cooling and power after moving the
computer; a separate temperature logger must be restarted separately if wanted.

This is an explicit audit-only continuation, not a general source-change
override. The new clean commit must descend from the previous execution and the
reviewed audit fix `828002a`; changes since the original execution are restricted
to the runner/continuation plumbing, its tests and operator guide. Inputs,
protocol, numerical environment and solver settings must remain unchanged.

Before launch, the parent reaudits the retained prefix. The first transition
keeps `audit-continuation.json`; subsequent transitions append
`audit-continuation-001.json`, `-002.json`, etc. Each links to its predecessor by
hash and records the old/new execution contexts, original binding identity,
historical evidence hashes, accepted count and next request. Existing binding,
continuation and attempt files are never replaced. Interrupted attempts retain
their evidence and retry the same policy slot in a new attempt directory.
Worker and analyzer resolve each attempt to its own execution version, including
all previous versions rather than treating the newest version as universal.

For later corrections, `--continue-from` names the **latest bound execution
commit**, not the original study commit. Ordinary same-commit resumes omit it.
If binding succeeds but launch is interrupted, retrying the identical command
is also safe: it reuses the existing binding and does not append another one.
Preflight takes the supervisor lock and requires no active or blocking attempt;
it does not reconcile an unfinalized attempt or remove a STOP marker. Ordinary
resume performs stopped-run reconciliation. Analysis reports the original
context, the latest continuation, and the complete ordered continuation list.

## Audit and retained evidence

```bash
uv run --extra dev python -m experiments.case118_tracy_2021.run_stage_d analyze
```

The analyzer independently reconstructs device/network feasibility and costs
from the public results and approved inputs, checks the retained x0 against
assigned values, and rebuilds state advancement and recovery ordering. It can
describe a partial run without treating an active or unfinalized attempt as an
accepted action. Its own context is recorded separately from execution context.

Workers audit the serialized list/scalar representation of the result, just
as the parent does, so array memory layout cannot change summation order across
the archive boundary. Worker/parent numerical audit comparisons allow roundoff
(`rtol=1e-12`, `atol=1e-12`; limits use relative tolerance only). For residuals,
the absolute comparison allowance is also at least 0.001% of the tighter
physical limit, in that residual's units. This is a comparison-resolution
choice, not a universal floating-point error bound. It accommodates retained
pre-normalization audits without changing their evidence. Schema, acceptance labels,
failure reasons and every residual's individual threshold decision must agree.
The physical acceptance tolerances are unchanged. Mismatch errors identify the
specific field rather than only reporting a generic audit disagreement.

The initial execution at `f7aec7c` stopped after seven accepted actions because
exact audit equality rejected a load-reporting residual of zero versus
`1.32e-23` MW in the eighth archived attempt. Both audits passed. This correction
can validate that archive without another solve; it does not change its bytes
or itself authorize a cross-version resume. The explicit one-time continuation
command above records the separately approved source transition.

The continued run later stopped at hour 8581, W=6, after an iteration-limit
failure. The worker correctly rejected the solve, but the parent compared a
load-total reporting residual of `4.66e-10` MW with the worker's zero using the
old `1e-12` absolute allowance and stopped before recovery. Both residuals pass
the unchanged `1e-4` MW check; the solve itself fails many other checks. The
comparison and serialization correction allows read-only reconstruction to
select `causal_1` without advancing this action or re-solving the primary.
The successive-continuation procedure above preserves that failure and the
earlier continuation while binding the reviewed correction before execution.

- `binding.json`: original study specification and source/environment identity.
- `trajectory-NN/hour-NN/attempt-NNN/`: request, source references, named start,
  complete canonical x0, native log, phase events, resource samples, result,
  completion hashes and supervisor outcome. Scientific records are not overwritten.
- `progress.json`: replaceable view reconstructed from those records.
- `stopped.json`: most recent stop reason (a convenience view).
- `invocations/invocation-NNN/`: UTC-anchored timing snapshots and an immutable
  finish record for each orderly run/stop/resume, preserving all stop reasons.
- `study-result.json`: terminal comparison record (possibly with incomplete
  trajectories); `complete` and `finished` are distinct.
- `analysis.json`: replaceable independently reconstructed summary.

The solve-interface clock includes canonicalization, x0 capture and native
execution. Per-phase and archive timing are retained separately; use solver
statistics/native logs for native-only time when available, not a relabeled
interface clock. Resource samples contain UTC anchors and monotonic elapsed time.
Total observed effort includes failed, interrupted and recovery attempts.
The analyzer reports invocation wall time separately from worker attempt effort.
Parent time includes preparation, independent auditing, cursor publication and
startup reconstruction; it is elapsed wall time, not CPU utilization. Per-action
spans distinguish preparation, worker supervision and parent finalization.
Startup replay is invocation overhead, not charged again to completed actions.
Finalized actions also retain total calendar latency from first preparation to
accepted cursor publication, including any intervening stops and restart replay.
The active per-action span sum excludes downtime and unallocated invocation
overhead; it must not be relabeled total calendar latency.

Gaps between orderly invocations are reported separately as downtime. An abrupt
termination without a finish record leaves a lower-bound snapshot: its unseen
tail cannot be separated from downtime and is explicitly labeled unknown.
Timing-record write overhead is not separately measured. UTC-based calendar
comparisons assume the host wall clock remains consistent; invocation durations
use a monotonic clock and do not span reboots.

After this comparison, pause for the owner's evaluation. Completing it does not
authorize more periods, changed look-aheads, or an annual AC launch.

## Pre-execution implementation checks

The implementation checkpoint passed 107 focused tests: 99 across the Stage D
selection/runner and Stage B/C regression files, plus eight existing AC
start-conversion/shift tests. Tests cover analytic AC physics and deliberate
result corruption, all four horizon shapes, actual Tracy model construction,
canonical x0 capture with native execution intercepted, finite recovery order,
manual subprocess stop/reaping, accepted-archive/cursor interruption, complete
trajectory skipping, cumulative effort on resume, controlled-clock repeated
stop/resume accounting, and abrupt-interruption timing uncertainty. Owner-data construction
tests skip explicitly when the ignored source/annual archives are unavailable;
synthetic scientific and restart tests remain clone-ready.

No native IPOPT solve or Stage D scientific execution was performed. The full
numerical suite was not run for this checkpoint. Ruff and whitespace checks pass.
Configured mypy passes for the changed private package helper; standalone
`--strict` reports the existing `_build_window` Any-return issue, independently
confirmed on the committed baseline as well. It was not changed in this slice.

The scientific-register self-check focused on units/state/target alignment,
independent AC reconstruction, restart accounting and retained failures. An
independent runner review and owner launch decision remain outstanding.
