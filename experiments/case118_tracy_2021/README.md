# Tracy 2021 → Case118

Stage A inputs are approved and the 72-arm Stage B DC comparison is complete.
The owner approved proceeding with Stage C at rho = 1/3 and lambda = 0.01.
Both annual DC solves are now complete and independently accepted. The
[annual comparison report](STAGE_C_REPORT.md) and results notebook are ready
for user review 2. AC execution remains separately gated.

Start with [the input report](STAGE_A_REPORT.md), then the
[study plan](../../plans/case118-tracy-2021-study-plan.md).

## Reproduce the input package

The source CSV is **owner-provided and Git-ignored**, not included in a fresh
clone. Obtain it from the study owner and place it at
`experiments/battery_terminal/data/9q9wtp_gen_and_load.csv` with SHA-256
`45e11f061d736741b18334aea0e9525c355c1a13068c291c1db6ed2e614b1b6f`.
Preparation refuses missing or substituted data; there is no synthetic fallback.
The retained tables and figures can be inspected without this file, but full
input regeneration and source-dependent integration tests require it.

From the repository root, with the standard package environment and plotting extras:

```sh
uv sync --extra dev --extra notebook
uv run --extra dev pytest tests/test_case118_tracy_inputs.py -q
uv run --extra notebook python -m experiments.case118_tracy_2021.prepare \
  --output experiments/case118_tracy_2021/results/my_inputs \
  --review-output experiments/case118_tracy_2021/results/my_review
uv run --extra notebook python -m experiments.case118_tracy_2021.render \
  --output experiments/case118_tracy_2021/results/my_inputs \
  --review-output experiments/case118_tracy_2021/results/my_review
```

Use fresh destinations: preparation refuses to overwrite an existing package.
Defaults produce ignored `results/stage_a_active_inputs/` and the reviewable
`stage_a/` package. Raw hourly device arrays remain in the ignored directory;
they regenerate from the pinned owner-provided CSV and seeded mapping. Array digests
use the existing shape-aware float64 hashing convention. Software/source hashes
and the full realized mapping are retained, not just a seed.

`prepare.py` prepares arrays and tables; `audit.py` independently reads source
rows and reconstructs allocated channels and totals. `model_inputs.py` supplies
one public-API device fleet to all formulations. Tests build three-hour models,
including reordered input columns, but never canonicalize or solve them.
Source-dependent tests explicitly skip when the owner CSV is absent; a present
file with the wrong hash fails rather than skipping. Clone-ready parser tests
use synthetic fixtures for valid calendars, missing/duplicate hours,
negative/nonfinite values, and missing/substituted files. Synthetic data is
test-only and is never used for the study's integration evidence.
`render.py` reads saved arrays/tables and reuses the historical calendar layout.
Historical inputs and evidence are read-only.

Generation IDs in the review table identify source rows; the current public
generator class does not expose `device_id`. Renewable/load/storage identities
are passed through their supported public fields. The public generators retain
source order in every formulation.

The active 5,000 MW cap, storage sizes, source and siting are approved.
Stage B now has six approved windows and a 72-solve comparison grid. The
[protocol](STAGE_B_PROTOCOL.md) documents the runner, explicit solver/audit
settings, resource limits and launch/analysis commands. Two batches stopped at
iteration limits; the second retained 21 accepted arms. The owner-approved
5,000-iteration-cap batch at `bdaeda4b8` subsequently completed all 72 arms in
`results/stage_b_maxiter5000/`; prior evidence is preserved.
`model_inputs()` applies the approved annual
50% endpoints even to an inspection window; numerical short-window studies
must explicitly select their boundary conditions before using it to solve.

## Explore retained comparison results

Initial owner observations and implications for MPC and uncertainty studies
are captured in [the Stage B discussion report](STAGE_B_REPORT.md).

`results_notebook.py` is a read-only marimo explorer of the completed 72-arm
`results/stage_b_maxiter5000/` batch. From the repository root:

```sh
uvx marimo run --sandbox experiments/case118_tracy_2021/results_notebook.py
```

The segment dropdown and three independent model multiselects control input,
aggregate, and device-level time-series plots. Device selectors cover generation,
storage, renewables, loads, branch flows, and nodal injections. Dates retain the
source's fixed UTC−08:00 timezone; SoC includes the initial boundary.

The notebook requires the ignored retained run directory and
`results/stage_a_active_inputs/aggregate_inputs.csv`, along with the tracked
Stage A tables and source network. It checks input/result hashes, does not solve
models, and does not substitute data when artifacts are absent. Its dependencies
are declared in the notebook and installed in an isolated environment by
`--sandbox`; no project dependency changes are needed.

## Annual DC pair (Stage C)

The [Stage C protocol](STAGE_C_PROTOCOL.md) fixes the matched annual pair,
unchanged input fleet and audit, and owner-approved **4-hour / 16-GiB per-worker
limits**. `run_stage_c.py` reuses Stage B's sequential fresh-process runner;
single-node runs first, then lossy DC, stopping on any rejected arm without retry.
The accepted run is retained in `results/stage_c/` from execution commit
`034ea6b9e5d9dd4276d6e742847f547c19a1047d`; do not rerun it to view results.
Both full-year results, their independent analysis, and the annual notebook
form user review 2 before any AC work.

```sh
uvx marimo run --sandbox experiments/case118_tracy_2021/annual_results_notebook.py
```

The notebook provides full-year input/output heatmaps, matched scales, signed
network-minus-copper-plate differences, interactive date ranges and device
time series, monthly totals, timing, congestion, and acceptance residuals.
It requires the ignored Stage C archives and Stage A aggregate inputs, plus
the tracked input tables and network source; neither the owner CSV nor a
solver installation is needed merely to view the retained results.
Compact numerical evidence is tracked in `stage_c_summary/`; the shared
read-only analysis lives in `annual_results.py`.

The owner subsequently approved deriving shards from the accepted lossy-DC
trajectory. The [boundary report](SHARD_BOUNDARIES.md) and
[state manifest](SHARD_BOUNDARIES.json) record the resulting 13-shard partition,
including its allowed 68-hour final shard. This calculation does not authorize
AC execution or select an AC runner policy.

## Bounded AC qualification (Stage D)

The owner has opened Stage D after committing the shard calculation at
`e09af9dc7`. The [qualification protocol](STAGE_D_PROTOCOL.md) starts with four
periods (surplus, deficit, and both transition directions), six implemented
hours each, at 1/3/6/12-step look-aheads: 96 controlling solves before recovery.
The owner approved sequential recovery, a 16 GiB worker RSS ceiling, IPOPT's
built-in iteration limit and no additional time cutoffs. Exact starts and
numerical/recovery settings remain to be frozen. Then pause for owner evaluation
before selecting the broader test set. The runner must checkpoint accepted
actions and resume each trajectory from its own realized state and shifted-start
source, skipping completed work; restart tests are required before execution.
No Tracy AC solve or annual AC launch
has occurred.
