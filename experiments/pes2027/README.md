# PES 2027 paper appendix and reproduction

This folder accompanies **Network-Model Fidelity in Intertemporal Storage
Dispatch: A Matched Comparison of Four Power-System Models**, by Bennet Meyers.
It connects the paper's six matched Case118 treatments, fixed battery-power
transfers and three-hour AC rollouts to their input preparation, numerical
protocols, retained evidence and figure-generation tools.

[Appendix](APPENDIX.md) gives the notation, complete mathematical models,
numerical criteria, qualifications and result tables. The manuscript remains
in the author's local drafting directory. Paper-specific plotters, figure
guidance and caption notes live here; numerical experiment code, protocols and
retained run artifacts remain in their original directories. This folder is
the paper's repository entry point, not a replacement for those scientific records.

## Folder contents

| File | Purpose |
| --- | --- |
| `APPENDIX.md` | Complete mathematical models, numerical criteria, qualifications and additional results |
| `PES_FIGURES.md` | Figure inventory, extraction scope, rendering commands and presentation conventions |
| `figure_notes.md` | Caption drafts, interpretation and scientific qualifications; copied into each result-figure bundle |
| `plot_pes_results.py` | Non-solving joint/transfer/rollout extraction and comparative figure rendering |
| `plot_pes_soc.py` | Non-solving input and normalized battery-energy time-series figures |
| `analyze_rollout_controls.py` | Non-solving trajectory distances for the six full AC-signposted controls in Appendix A5 |

Invoke the plotters as `experiments.pes2027.plot_pes_results` and
`experiments.pes2027.plot_pes_soc` from the repository root. They continue to
read the original study directories; relocating the tools does not relocate
or rewrite historical evidence. Their regression tests remain under `tests/`.

## Reproduction levels

| Task | Required material | Optimization |
| --- | --- | --- |
| Inspect methods and compact evidence | Repository, appendix and linked protocols | None |
| Regenerate inputs | Repository and pinned owner-provided Tracy CSV | None |
| Reconstruct paper metrics and figures | Retained joint, transfer and rollout archives, input identities and preparation references | None |
| Repeat numerical comparisons | Complete inputs/reference fixtures, compatible solver environment, clean reviewed source and newly approved resource/launch records | New solves |

Raw runs and the Tracy source CSV are Git-ignored. A fresh clone contains the
code, protocols, Stage A review tables and selected certificate evidence, but
does not contain every dependency needed to regenerate the paper figures.
Obtain the retained archive package from the author before attempting that
workflow. The following instructions describe reproduction; they do not
authorize a new numerical run or reuse of an earlier execution authorization.

## Environment and inputs

Work from the repository root. Follow the [package installation instructions](../../README.md)
for IPOPT and `cyipopt`, then install the development and plotting extras:

```sh
uv sync --extra dev --extra notebook
```

For historical numerical reproduction, use the dependency lockfile and resolved
package/native-library versions recorded by the relevant execution context,
rather than treating the latest environment as identical. Numerical results
can vary with the platform, solver version and local AC initialization.

The input file is
`experiments/battery_terminal/data/9q9wtp_gen_and_load.csv`, with SHA-256
`45e11f061d736741b18334aea0e9525c355c1a13068c291c1db6ed2e614b1b6f`.
The [Stage A report](../case118_tracy_2021/STAGE_A_REPORT.md) and
[input instructions](../case118_tracy_2021/README.md#reproduce-the-input-package)
describe its calendar, scaling, seeded spatial allocation and independent audit.
There is no substitute or synthetic-data fallback for this study.

To regenerate into fresh inspection destinations without overwriting retained inputs:

```sh
uv run --extra notebook python -m experiments.case118_tracy_2021.prepare \
  --output experiments/case118_tracy_2021/results/pes_inputs_reproduction \
  --review-output experiments/case118_tracy_2021/results/pes_input_review_reproduction
uv run --extra notebook python -m experiments.case118_tracy_2021.render \
  --output experiments/case118_tracy_2021/results/pes_inputs_reproduction \
  --review-output experiments/case118_tracy_2021/results/pes_input_review_reproduction
```

Compare realized device IDs, tables and shape-aware array digests with the
tracked [Stage A manifest](../case118_tracy_2021/stage_a/manifest.json).
The original study readers use `results/stage_a_active_inputs/`; inspection
outputs at a different path do not silently replace that bound input package.

## Retained evidence needed for the paper

Preserve the original relative paths when installing an author-supplied archive
package. Current readers deliberately check those paths and their recorded hashes.

| Evidence | Repository-relative retained location | Role |
| --- | --- | --- |
| Annual device inputs | `experiments/case118_tracy_2021/results/stage_a_active_inputs/` | Shared input arrays and calendar |
| Prepared joint cohort | `experiments/case118_tracy_2021/results/e3_prepared/` | Five treatments, four models each |
| Forced-shedding joint cohort | `experiments/case118_tracy_2021/results/e3_plus/` | Sixth treatment, four models |
| Forced fixture reference | `experiments/objective_assembly_qualification/results/qualification_002/binding.json` | Exact E3+ mathematical inputs and shortage witness |
| Fixed-power transfers | `experiments/tracy_battery_transfer/results/transfer_001/` | Eighteen original outcomes, including unresolved attempts |
| Energy-signpost rollouts | `experiments/tracy_ac_rollout/results/rollout_001/` | Twenty-four trajectories, including their continuation generation |

Include the completion manifests, supervision, input/source bindings,
native/preparation/start evidence, logs and any referenced prior evidence;
copying only result arrays is insufficient for admission or independent replay.
The per-run bindings and figure snapshot's `sources` registry identify additional
dependencies. E3+ requires its pinned fixture binding at SHA-256
`4d4ec1cb451207f4d0b0a17047258f902110c886bc73513ec3a1a152248b2d1f`.

The joint-cohort execution commits are
`2edc6d3bbeac85bd78f5bf6d9cdc387f851eb0f0` (prepared E3) and
`13ad35bc5f0e9f8b5d20d54a55d2ab2cd7a26df8` (E3+).
The rollout starts at `8267dbf857872e4b6adcbf6c84dc05f002d63f60`
and has a separately bound operational continuation at
`b260e354d6eb63dfa559af7b13996617e9cdcd7a`.
Read all generations together; neither continuation nor later analysis replaces
the original attempt records. Transfer execution identity is in its retained
`binding.json`; every arm retains its own source and completion identities.

Two snapshots used by the current manuscript are:

| Snapshot | SHA-256 |
| --- | --- |
| `experiments/tracy_battery_transfer/results/transfer_001/report.json` | `3375d14c9d50a0a1a4d8439e3f1950313dfe373820a38ef667e35188383266a5` |
| `experiments/case118_tracy_2021/results/pes_figures_review_001/figure_data.json` | `0f07ff41289529b0d496f3085351b77f9c1817cf31512830fe3fffc6f5a27462` |

These identify historical inputs to the paper, not a promise that rerunning a
nonconvex solver will reproduce every coordinate or artifact byte.

## Regenerate metrics and figures without solving

The current manuscript uses the standardized model names **copper plate**,
**network flow (NF)**, **SOCP** and **full AC**. Its current label-only renderings
are `pes_figures_names_001/` and `pes_soc_names_001/` under
`experiments/case118_tracy_2021/results/`. Their numerical snapshots are
byte-identical to the original bundles identified above; historical evidence
and internal formulation keys are unchanged.

The [figure guide](PES_FIGURES.md) and
[caption notes](figure_notes.md) describe extraction scope,
units and axes. From the repository root, choose fresh, unused output directories:

```sh
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache \
MPLCONFIGDIR=/private/tmp/cvxopf-pes-mpl \
uv run --extra notebook python -m experiments.pes2027.plot_pes_results \
  --output experiments/case118_tracy_2021/results/pes_results_reproduction

UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache \
MPLCONFIGDIR=/private/tmp/cvxopf-pes-mpl \
uv run --extra notebook python -m experiments.pes2027.plot_pes_soc \
  --output experiments/case118_tracy_2021/results/pes_soc_reproduction

UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache \
MPLCONFIGDIR=/private/tmp/cvxopf-pes-mpl \
uv run --extra notebook python -m experiments.pes2027.plot_pes_network \
  --output experiments/case118_tracy_2021/results/pes_network_reproduction
```

The first command emits the combined joint/transfer/rollout figure
`01_03_04_results.pdf`, separate panels, `figure_data.json` and a hashing manifest.
It reconstructs executed rollout physics and accounting, verifies source and
state continuity, and preserves historical joint/transfer qualifications.
It does not replay every losing attempt or verify the entire native-start and
recovery history. The second emits all six input/SoC panels and `soc_data.json`;
the manuscript uses the deficit-to-surplus panel `soc_case_4.pdf`.

For a layout-only iteration, `--render-only` uses a bundle's saved numerical
snapshot and updates its rendering artifacts. Use a fresh full extraction
instead when changing scientific calculations or validating the raw evidence.
The third command emits `configured_network.pdf` and PNG plus a hashing manifest,
using identity-checked Stage A siting tables and the pinned PGLib network.
The manuscript uses this configured-network panel directly. The layout seed,
solar/wind and battery area scales match the historical view; gray load-node
areas now scale with Case118 source MW demand, whose shares define configured
load allocation. Marker scales differ by family. Historical Stage A images
remain unchanged.

Non-solving figure regressions:

```sh
uv run --extra dev pytest tests/test_pes_result_figures.py tests/test_pes_soc_figures.py tests/test_pes_network_figures.py -q
```

To reproduce the six full AC-signposted trajectory controls without solving,
choose an unused result filename:

```sh
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache \
uv run python -m experiments.pes2027.analyze_rollout_controls \
  --output experiments/case118_tracy_2021/results/pes_joint_ac_controls_reproduction/distances.json
```

The calculation uses all 27 batteries and the 23 interior boundaries, with equal
device weighting after capacity normalization. It retains mean and maximum
individual deviations, fleet mean deviations, cost differences and source hashes.
The reader verifies the retained checkpoint/window/controller identities and
reconstructs energy from executed powers. Test the distance calculation with
`uv run --extra dev pytest tests/test_pes_rollout_controls.py -q`.

## Repeat the numerical study

Follow these protocols in order, using fresh output roots and reviewed clean
source/environment bindings. Display/preflight commands are distinct from
execution commands; some preflights build and canonicalize models even though
they do not optimize.

1. [Prepared E3](../case118_tracy_2021/E3_PREPARED_PROTOCOL.md): solve the five
   matched 24-hour treatments under four network models. `run_e3 --prepared`
   supplies protocol display, preflight, explicit execution and status modes.
2. [E3+](../case118_tracy_2021/E3_PLUS_PROTOCOL.md): add the frozen forced-shedding
   treatment using `run_e3 --plus`. Preserve its qualified convex source outcomes.
3. [Fixed battery-power transfers](../tracy_battery_transfer/PROTOCOL.md): admit
   the two joint cohorts and run one AC attempt per convex donor/treatment through
   `experiments.tracy_battery_transfer.run`. No repair or automatic retry is part
   of this comparison.
4. [Energy-signpost rollouts](../tracy_ac_rollout/PROTOCOL.md) and
   [runner instructions](../tracy_ac_rollout/README.md): admit all joint signposts,
   prepare explicit budgets, separately review an execution authority and run
   the 24 arms with `experiments.tracy_ac_rollout.run`.
5. Independently analyze finalized evidence, then regenerate the paper metrics
   and figures. Keep solver outcomes, physical acceptance, economic accounting
   and later incompatibility certificates distinct.

The rollout's historical run-wide ceilings were 2,304 launches and 172,800
supervised worker-seconds, with 300-second build and post-solve ceilings and
one-second polling. Its primary remained running when a shared helper became
eligible after 300 solve-phase seconds. These are historical settings, not a
fresh authorization or a five-minute primary timeout. New budgets and any
source-path configuration must be reviewed before rerunning the matrix;
current downstream readers are bound to the historical cohort paths.

The non-solving [SOCP transfer preflight](../tracy_socp_transfer_preflight/README.md)
and [multigraph certificate report](../tracy_socp_transfer_preflight/MULTIGRAPH_REPORT.md)
document the separate loop analysis. A numerical transfer rejection is not an
infeasibility proof; the certificate applies only where its necessary-condition
and interval-coverage checks succeed.
