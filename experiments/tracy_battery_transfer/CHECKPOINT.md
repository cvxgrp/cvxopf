# Battery-transfer preparation checkpoint

Prepared on October 8, 2026, for owner review and commit. Execution has not been
launched; launch approval is separate. This checkpoint does not establish AC
compatibility for any donor schedule.

## What is ready

The protocol and runner cover all three convex donor models in all six retained
Tracy scenarios, including forced shedding: 18 sequential AC cases. Each uses
the approved component-first production preparation and the matched retained
AC estimate as its starting point.

Every hourly battery real-power value is pinned without rounding or repair.
The battery API removes the terminal equality; initial SoC and storage dynamics
remain. The original endpoint is retained for independent checking. Generation,
renewables, shedding, reactive dispatch, and AC network decisions remain free.

No production sources or historical run artifacts were changed for this
experiment. The earlier notebook and sensitivity changes already in the working
tree remain separate, untouched by this preparation.

## Verification

Preflight reconstructed and canonicalized all 18 actual transfer graphs without
calling an optimizer or creating an execution directory. Source hashes,
device ordering, matched AC inputs, physical starts, and cost-coordinate
handoffs passed their checks. All source schedules passed the storage-only
checks; the largest implied endpoint difference was approximately
`2.17044e-8 MWh`, below the existing `1e-3 MWh` endpoint allowance.

The experiment has 31 non-solving regression checks covering API endpoint
removal, full-hour power pins, storage dynamics, unchanged source values,
cost-coordinate transformation, native restoration, archive tampering,
unfinished attempts, clean-source authority, serialization, supervision, and
no-retry resource accounting. With the 12 existing result-viewer/sensitivity
checks, all 43 focused checks passed. Ruff and whitespace checks passed.
Pytest also emitted the existing macOS warning about importing both CVXOPT and
cyipopt; no numerical optimizer was invoked in these checks.

Successful native inverse-replay tests use synthetic canonical points, with
physics mocked explicitly. They test replay mechanics, not physical feasibility.
The eventual experiment still requires full native convergence and independent
AC physics, accounting, pin, endpoint, and forced-shedding checks.

## Reviewable files

- `model.py`: source selection, battery API construction, initialization, audits,
  and descriptive comparisons against matched unrestricted AC estimates.
- `run.py`: preflight, source/environment binding, supervised execution,
  immutable archives, and independent read-only status replay.
- `test_transfer.py`: portable non-solving checks plus optional retained-source
  checks; use the explicit command in the protocol.
- `PROTOCOL.md`: scientific question, fixed/free decisions, interpretation,
  approved limits, and commands.
- `.gitignore`: excludes this experiment's raw `results/` only.
- `CHECKPOINT.md`: this preparation record.

Suggested commit message for these files:

```text
Add AC battery schedule transfer experiment
```

After owner review and a clean commit, request launch approval before running
the command in the protocol. No retries or recovery ladder are planned.
