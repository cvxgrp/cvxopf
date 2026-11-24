# Reproduce the Case118 PES experiments from a fresh CVXOPF clone

This ZIP supplies the inputs missing from Git. You will generate your **own**
24 joint solutions, use their battery powers in 18 fixed-schedule AC transfers,
and use their battery energies as targets in 24 three-hour AC rollouts. The ZIP
contains none of those solutions. Run the steps in order in **one fresh clone**:
the recorded code revisions change, but your ignored result directories stay in place.

## 1. Install the inputs and environment

Use a fresh clone of <https://github.com/cvxgrp/cvxopf>. The historical joint
and transfer runners require macOS, AC power, working `ps` process/RSS queries,
and CPU/GPU temperatures from `macmon`. They will not launch unchanged on Linux
or with unavailable telemetry. The study used Apple Silicon, Python 3.11.15,
CVXPY 1.9.3, CLARABEL 0.11.1 and native IPOPT 3.14.19. Install Git, `uv`,
IPOPT and its development files (`pkg-config`), and `macmon` before proceeding.
The lockfile pins Python packages, not the system IPOPT build.

From the **clone root**, in a POSIX-compatible shell, set the ZIP's absolute path.
Extract only `experiments/*` into the clone; do not replace the repository README.
Keep the default run directory names: downstream programs read those paths.

```sh
archive=/absolute/path/to/pes2027-reproduction-inputs.zip
unzip -tq "$archive"
git switch --detach 2edc6d3bbeac85bd78f5bf6d9cdc387f851eb0f0
unzip -n "$archive" 'experiments/*'
git status --porcelain                # must be empty
syncenv() { uv sync --frozen --python 3.11.15 --extra dev --extra notebook; }
syncenv
tracy=experiments/case118_tracy_2021
joint() { uv run --no-sync python -m experiments.case118_tracy_2021.run_e3 "$@"; }
uv run --no-sync python -m experiments.case118_tracy_2021.prepare \
  --output "$tracy/results/stage_a_active_inputs" \
  --review-output "$tracy/results/regenerated_stage_a_review"
```

This regenerates the annual inputs in the location the runners read, without
overwriting the **tracked** Stage A manifest or device tables. Stop on any failed
command or hash mismatch. Do not use an existing study directory.

### What is in the ZIP?

The only external **source data** is
`experiments/battery_terminal/data/9q9wtp_gen_and_load.csv`
(SHA-256 `45e11f061d736741b18334aea0e9525c355c1a13068c291c1db6ed2e614b1b6f`).
These are modeled Tracy, California demand, utility solar, utility wind and
distributed solar profiles. Preparation selects the 8,760 hourly intervals of
2021 in fixed UTC−08:00 and allocates them to the PGLib IEEE 118-bus test case
in Git. Appendix A1 in the paper's repository folder describes the upstream
models and the mapping. Tracy supplies the hourly conditions, not the topology.

The other **nine files** are earlier study records the published code checks:

| Repository-relative location | Files and purpose |
| --- | --- |
| `experiments/case118_tracy_2021/results/stage_c/` | `binding.json`, `study-result.json`, `analysis.json`; and `completion.json` and `result.json.gz` in **each** of `arm-000/` and `arm-001/`. These seven files identify the annual DC runs used to select the published 24-hour windows. |
| `experiments/objective_assembly_qualification/results/qualification_002/binding.json` | Exact physical input specification for forced shedding: same deficit window and battery endpoints, with requested P and Q both multiplied by 7.5416905141946184. |
| `experiments/case118_tracy_2021/results/e3/binding.json` | Earlier study identity checked by the transfer and rollout code. It supplies no donor solution. |

## 2. Solve the 24 joint problems

The first revision solves five 24-hour scenarios under copper plate, network
flow, SOCP and full AC. The second adds the forced-shedding scenario under the
same four models. Use the **two distinct commits** below: the transfer reader
checks which commit produced each cohort. Preflights do not optimize; the
`--authorize-execution` commands start numerical work.

```sh
# Still at 2edc6d3b… from step 1
joint --prepared --commit "$(git rev-parse HEAD)" --preflight
joint --prepared --commit "$(git rev-parse HEAD)" --authorize-execution
joint --prepared --status

git switch --detach 13ad35bc5f0e9f8b5d20d54a55d2ab2cd7a26df8
syncenv
joint --plus --commit "$(git rev-parse HEAD)" --preflight
joint --plus --commit "$(git rev-parse HEAD)" --authorize-execution
joint --plus --status
```

Your joint records are now in `$tracy/results/e3_prepared/` and
`$tracy/results/e3_plus/`. Inspect the statuses, not just the process exit code.
The downstream reader requires all 20 ordinary arms to pass. For forced shedding
the published comparison also used three converged convex schedules with flagged
audits; retain the actual flags and review any qualified use for **your** run.
A missing trajectory or unconverged solve is not a donor. The published ordinary
run raised its per-attempt wall ceiling from 1,800 to 3,600 seconds during
execution; the default starts at 1,800. Record any budget revision using the
runner's protocol mechanism, not by editing a result or changing tolerances.

## 3. Run the 18 fixed-power transfers

```sh
git switch --detach 4fbd85f3bd80a573edac4da284e325804f1d77a3
syncenv
transfer() { uv run --no-sync python -m experiments.tracy_battery_transfer.run "$@"; }
transfer --preflight
```

Preflight checks each battery's power schedule, verifies that the implied stored
energy stays within bounds and reaches its target, and builds the AC model with
battery power fixed to that schedule. It does not establish that the AC network
can accommodate the schedule.

Before authorizing transfers, inspect the
three forced-shedding convex donors from your run. This read-only command uses
the transfer reader's own rule to select each result:

```sh
uv run --no-sync python - <<'PY'
import gzip, json
from pathlib import Path
from experiments.case118_tracy_2021.e3_results import result_reference

root = Path("experiments/case118_tracy_2021/results/e3_plus")
for item in json.loads((root / "progress.json").read_text())["arms"]:
    model = item["arm"]["formulation"]
    if model == "ac":
        continue
    path = root / result_reference(item, "prepared_plus")["path"]
    with gzip.open(path, "rt") as f:
        r = json.load(f)
    audit = r["audit"]
    print(model, path, "native:", r["native"]["status"], "classification:", r["classification"])
    print("Physical/accounting audit passed:", audit["passed"])
    for name, limit in audit["limits"].items():
        value = audit["residuals"][name]
        if not value <= limit:
            print(f"  {name}: {value:g} (limit {limit:g})")
    print("Independent audit:", json.dumps(r["independent_audit"], indent=2))
PY
```

In the published run, CLARABEL reported `Solved` for all three donors, but each
failed the independent cost check. SOCP also exceeded the limits for injection
reporting and for active and reactive power balance. That qualification does not carry over to
your run. Stop if evidence is missing; assess any failed check and the size of
its discrepancy before authorizing the transfers:

```sh
transfer --authorize-execution --commit "$(git rev-parse HEAD)"
transfer --status
```

For each scenario, the runner fixes battery power to each of your three convex
joint schedules and initializes the remaining variables from your joint AC
solution. It writes `experiments/tracy_battery_transfer/results/transfer_001/`.
Keep all 18 outcomes, including rejections or timeouts. A failed transfer is
not by itself proof of infeasibility and does not disqualify its joint schedule
as a source of energy targets.

## 4. Run the 24 energy-signpost rollouts

Each rollout targets the individual battery energies from **a joint solution**,
including the joint AC control. It solves a three-hour AC window (shortened at
the end), executes its first action, and carries the realized state forward
by one hour. Your fixed transfers are used for comparison, not as targets.

The published run started at `8267dbf857872e4b6adcbf6c84dc05f002d63f60`
and continued at `b260e354d6eb63dfa559af7b13996617e9cdcd7a` after a telemetry
fix. Use the latter for a **new** run; do not replay the old interruption.
Keep your controls outside Git and review the resources before launch. These
are the historical ceilings (worker time is cumulative, not elapsed time):

```sh
git switch --detach b260e354d6eb63dfa559af7b13996617e9cdcd7a
syncenv
rollout() { uv run --no-sync python -m experiments.tracy_ac_rollout.run "$@"; }
runroot=experiments/tracy_ac_rollout/results/rollout_001
controls=$(mktemp -d)
printf '%s\n' '{"max_launches":2304,"total_worker_seconds":172800,"build_seconds":300,"post_solve_seconds":300,"poll_seconds":1}' > "$controls/budgets.json"
rollout --prepare --output "$runroot" --budgets "$controls/budgets.json"
```

Preparation includes the no-solve preflight. Inspect the generated
`binding.json`: source schedules, classifications, all 24 arms and budgets.
Only if you approve **this** binding and its resource use, create its launch
record and run. Two primary workers share one helper (up to three solver
processes); the per-worker and aggregate RSS limits are 16 and 24 GiB.

```sh
python3 - "$runroot/binding.json" "$controls/approval.json" <<'PY'
import hashlib, json, sys
from pathlib import Path
raw = Path(sys.argv[1]).read_bytes()
b = json.loads(raw)
approval = dict(schema=1, experiment="tracy_ac_rollout", execution_authorized=True,
                binding_sha256=hashlib.sha256(raw).hexdigest(), execution_commit=b["context"]["commit"],
                primary_workers=2, helper_workers=1, maximum_solver_processes=3,
                recovery_policy="causal_first_speculative_v1", recovery_revision=2, budgets=b["budgets"])
with open(sys.argv[2], "x") as f:
    json.dump(approval, f)
PY
rollout --run --output "$runroot" --authority "$controls/approval.json"
rollout --status --output "$runroot"
```

## Compare your results

Compare joint outcomes, fixed transfers and **executed** rollout actions
(not overlapping window forecasts) on device cost, energy not served,
curtailment and battery trajectories. Preserve classifications and missing
results. Expect platform-dependent timing and possible differences in local
AC solutions. The original study reported 24 joint solutions (including the
qualified results), 15 compatible transfers out of 18, and 24 completed
rollouts; these are comparisons, not targets to force.

The appendix and figure guide are available at reader commit
`3b918192e931d1cd88cd65a13d0c8caceea463b6` under `experiments/pes2027/`.
The historical figure tools are **not** general acceptance tests for a new
run: the results plot expects the original pattern of three failed transfers,
and the rollout SoC figure checks an original analysis-file hash. Use your
own run records to report a different outcome.
