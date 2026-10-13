# PES result-figure review drafts

Non-solving figure drafts, 2026-10-13. These are for visual iteration, not a
manuscript update or a new numerical study. Current output:
`experiments/case118_tracy_2021/results/pes_figures_review_001/`
(repository-root-relative; ignored PNG and vector PDF exports).
Paper-specific plotters and notes live here; retained evidence stays in the
original experiment directories.

| Figure | Content |
| --- | --- |
| `01_joint_dispatch` | Styled markers with stems from zero for six matched 24-hour cases and four models: common device cost, ENS and renewable curtailment. |
| 02, pending | Baseline/prepared numerical comparison; no completed matched results were available when these drafts were generated. |
| `03_fixed_schedule_transfer` | AC outcomes with fixed donor battery powers, relative to unrestricted joint AC. The three unresolved solver outcomes remain explicit; the caption separately identifies subsequent loop exclusions. |
| `04_ac_rollouts` | Executed-only 24-hour totals from three-hour AC rollouts, relative to joint AC, including the AC-signpost control. Axes match Figure 3. |
| `03_04_ac_implementation` | Manuscript combination: fixed-power transfers above rollouts, with one shared legend and the AC-signpost key explicitly rollout-only. The separate review plots remain available. |
| `01_03_04_results` | Full-page manuscript figure with one shared legend: (a) joint dispatch, (b) fixed-power transfers, (c) energy-signpost rollouts. Pink means joint AC in (a) and its energy-signpost control in (c), absent in (b). |

The plotter reuses `e3_results`, retained-transfer admission, and the rollout
`audit.trajectory` reconstruction. All 24 reconstructed rollout days pass its
physical/accounting checks; forced-shedding trajectories also pass the existing
input-only ENS witness. Source references, executed-window hashes, controlling
records, supervision and state continuity are checked. This is **not** a full
replay of native canonical starts, losing attempts, or recovery policy. Joint
and transfer classifications are historical retained evidence, not new solves
or new native acceptance decisions.

Common device cost excludes `dc_loss_cost`; integrated cost is not multiplied
by delta again. Energy sums use delta once. Rollouts aggregate only executed
first actions, never overlapping prediction objectives. Source qualifications
remain in the retained data and accompanying notes, not marker styling.
Numerical-scale positive and negative values are not clipped; symmetric-log
axis definitions and linear ranges are documented in the caption notes.
Local AC references are not globally optimal certificates. Signed differences
are descriptive, including near-zero negative values for the AC-signpost control.

`figure_data.json` retains unrounded metrics, source hashes and trajectory audits;
`manifest.json` hashes that snapshot, the PNG/PDF files and `figure_notes.md`.
The canonical [figure notes](figure_notes.md) are copied beside every rendering
bundle. They contain proposed LaTeX captions, interpretation and qualifications;
plots retain only panel labels, units, legends and minimal missing-data indicators.
No historical artifact is rewritten. Figures are 7.2 by 3.0 inches for compact
two-column PES placement (5.4 inches tall for the two-row combination and 8.2
for the full-page three-row figure), with unchanged 8-point labels and marker styles;
plot and legend marker sizes are 15% smaller than the initial review draft.
Vertical compression changes spacing, not values or axis definitions.

Generate a fresh bundle from retained owner-supplied archives:

```sh
XDG_CACHE_HOME=/private/tmp/cvxopf-pes-cache MPLCONFIGDIR=/private/tmp/cvxopf-pes-mpl \
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
VECLIB_MAXIMUM_THREADS=1 uv run --offline --no-sync --extra notebook \
python -m experiments.pes2027.plot_pes_results \
  --output experiments/case118_tracy_2021/results/pes_figures_review_002
```

For layout iteration only, add `--render-only` and point to an existing bundle.
That rereads the retained metric snapshot and overwrites only its review figures
and rendering manifest, not numerical evidence. It does not revalidate raw input
archives; use a fresh collection if the scientific extraction changes.

## Battery time-series trial

`plot_pes_soc.py` reads the finalized E3/E3+ inventory without building models
or solving. It retains boundary energies, capacity identities, source archive
hashes and execution commits in a separate `soc_data.json` snapshot. Review
outputs are in `experiments/case118_tracy_2021/results/pes_soc_review_002/`
(repository-root-relative); historical evidence is unchanged.

The single-column trial places common hourly requested demand and available
renewable power above stacked full-AC/copper-plate **joint** schedules. Gray SoC curves are
each device's energy divided by its capacity; bold black is total fleet energy
divided by total fleet capacity, not the unweighted mean of device percentages.
The initial boundary is included. The draft uses the deficit-to-surplus
treatment as an illustrative contrast, not a new selection or acceptance gate.
All six treatments are exported for author comparison.

```sh
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache MPLCONFIGDIR=/private/tmp/cvxopf-pes-mpl \
uv run --offline --no-sync --extra notebook \
python -m experiments.pes2027.plot_pes_soc \
  --output experiments/case118_tracy_2021/results/pes_soc_review_003
```
