# Implementation checkpoint

The initialization-only runner is implemented for original cases 13–15, the
ordinary neutral deficit-to-surplus interval [2439, 2463). No recovery solve has
been launched. The original transfer experiment and production code are unchanged.
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
use the documented execution command. Process/RSS permission, AC power and thermal
telemetry are rechecked before any numerical worker. There is no continuation,
target-free solve, automatic repair, resume or source-result reclassification.
