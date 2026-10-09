# Implementation checkpoint

The initialization-only runner is implemented for original cases 13–15, the
ordinary neutral deficit-to-surplus interval [2439, 2463). The original transfer
experiment and production code are unchanged.
The owner still controls review, staging, commit and separate launch authorization.

## Files

- `model.py` validates the completed origin and generates the declared seven-slot
  ladder per donor using production start/perturbation helpers. It retains exact
  battery powers and implied SoC in raw and assigned starts.
- `run.py` binds all prospective starts, requires a clean full execution commit,
  uses the existing sequential process supervisor, stops each donor at its first
  independently audited success, and independently replays status without solving.
  It records skipped/unlaunched slots, native rejection, timeouts, audits,
  infrastructure outcomes, cycling warnings, partial artifacts and worker effort.
- `PROTOCOL.md` specifies scope, immutable inputs, seeds, scales, resource ceilings,
  stopping/acceptance rules, qualifications and CLI commands.
- `test_starts.py` tests deterministic starts and canonical handoffs with optimizer
  calls forbidden, plus synthetic archive/supervision lifecycle failures. Synthetic
  lifecycle tests are not claims of physical feasibility.
- `.gitignore` keeps new raw results local to this experiment.

## Verification

The original transfer baseline passed 31 non-solving tests before implementation.
After implementation, 202 focused tests passed across the new/original experiments,
hierarchical vectorized recovery, preparation bridges and qualification. Existing
dependency/device/solver warnings remain visible; no environment changes occurred.
Ruff lint/format checks passed.

The final retained-data preflight rebuilt and canonicalized all 21 prospective
24-hour starts, with optimizer calls explicitly prohibited. All 235 origin-file
hashes matched before and after preflight. No execution directory was created.

The unperturbed matched-reference start, after replacement of b/implied SoC, has
maximum positive battery-circle residual `b^2 + b_q^2 - S_max^2` of approximately
223.869, 633.484 and 698.371 MVA² for SOCP, lossy DC and copper plate respectively.
The three fresh unperturbed flat starts have zero positive residual for this check.
Perturbed starts need not be feasible and may introduce additional violations.
These are initialization diagnostics only, not independently audited solution
residuals or proof that inverter-start inconsistency caused the original failures.

## Remaining execution boundary

Review the protocol and proposed ceilings (21 launches maximum, 3600 seconds and
16384 MiB per attempt, 75600 cumulative supervised worker-seconds). After owner
commit and separate launch approval, rerun preflight on the committed context and
use the documented execution command. Process/RSS permission and thermal
telemetry are rechecked before any numerical worker. Power source is informational.
There is no target-free solve, automatic repair or source-result reclassification.

## Power-stop correction and continuation readiness

The owner committed the original runner as
`3ea4e74bef1748e5af61df63035f672ff36443e7` and authorized execution. In
`results/starts_001`, call 1 (SOCP flat start) finalized as native rejection after
897 IPOPT iterations and 1431.43078375 supervised worker-seconds. The root then
stopped before call 2 because the original AC-power gate did not detect mains
power. No later slot was launched. This rejection is retained, not a proof of
physical incompatibility.

The owner authorized removing that gate and explicitly continuing the remaining
ladder. The correction is confined to `run.py`, `test_starts.py`, `PROTOCOL.md`
and this checkpoint. A fresh `results/starts_002` binding references and hashes
all 19 predecessor files without rewriting them. Completed outcomes, slots,
launches and supervised seconds carry forward. Worker 2 starts at trial 2,
matched-AC scale 1e-4/seed 17001311; worker 1 is never launched again. The original
global budget is not reset. Power-source telemetry is retained as a timing caveat.

The new clean commit is supplied at launch and is not hardcoded to the original
runner commit. Tests explicitly exercise a different execution commit, refuse
changed production sources, and rehearse the continuation supervisor with optimizer
calls forbidden. Historical replay retains its old binding identity; current
workers still enforce their new execution binding.

Verification of this correction: 209 focused tests passed (35 existing warnings),
Ruff checks passed, and the retained-data continuation preflight reconstructed all
21 identical canonical starts and independently verified the finalized predecessor.
The fresh run directory has not been created and no recovery solve was repeated.
The owner elected to make the corrective commit personally. After that commit,
rerun committed-context preflight and launch the already-authorized continuation
with the documented `--continue-from` command.
