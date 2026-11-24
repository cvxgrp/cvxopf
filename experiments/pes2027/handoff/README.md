# Reproduce the Case118 PES comparison from retained evidence

Start with the figures; no optimizer, Git checkout or software installation is needed
to **read** them. Unzip into a **new directory** (allow about 2–3 GB of disk space)
and open these files under `cvxopf/experiments/case118_tracy_2021/results/`:

- `pes_network_review_002/configured_network.pdf`: where the devices were placed.
- `pes_figures_names_001/01_03_04_results.pdf`: the joint dispatches, fixed
  battery-power transfers and three-hour AC rollouts, in that order.
- `pes_soc_names_001/soc_case_4.pdf`: the demand, available nondispatchable
  power and battery energy traces for the deficit-to-surplus day.

The detailed methods and figure guide are in `cvxopf/experiments/pes2027/`.
The manuscript, an installed environment, Git history and the upstream regional
data-generation pipeline are **not** in this archive. The snapshot is for inspecting
and replaying evidence; use a real Git checkout for new supervised solves.

## Where did the data come from?

There are two independent sources. **The topology is not Tracy's grid.**
The electrical case is PGLib-OPF's IEEE 118-bus transmission test system,
revision `dc6be4b2f85ca0e776952ec22cbd4c22396ea5a3`. Its original MATPOWER
file, CC BY 4.0 notice and upstream manifest are in
`experiments/case118_annual_hierarchy/source/` under the snapshot.

**The hourly conditions are modeled Tracy, California profiles, not
measurements.** The exact owner-supplied multi-year export,
`experiments/battery_terminal/data/9q9wtp_gen_and_load.csv`, is included.
Its SHA-256 is `45e11f061d736741b18334aea0e9525c355c1a13068c291c1db6ed2e614b1b6f`.
The study selects 8,760 consecutive hourly interval starts in calendar 2021,
fixed UTC−08:00 (no DST). It uses **four** channels:

| CSV column | Origin |
| --- | --- |
| `9q9wtp_load` | Modeled Tracy-node demand from the upstream western U.S. regional load study |
| `9q9wtp_solar` | Utility solar: USPVDB systems modeled with pvlib-python |
| `9q9wtp_wind` | Utility wind: USWTDB systems modeled with PySAM Windpower |
| `9q9wtp_dist_solar` | Distributed residential solar: county-level pvlib-python modeling informed by EIA-861M small-scale solar estimates |

The supplied CSV is the reproducibility boundary. Appendix A1 links the upstream
sources, including the [EIA-861M allocation note](https://github.com/slacgismo/regrow/blob/main/data/EIA861M_SmallScaleSolar.md).
The available study record does not specify every inventory release, weather input,
model version or parameter needed to regenerate that CSV from scratch. Nor does the
PGLib license grant redistribution rights for the Tracy profiles; check with the
provider before redistributing them beyond this collaborator handoff.

**How the two sources meet.** Stage A applies the same multiplier,
3.0548333420396085, to all four source channels so peak available net load
is 6,000 MW. Source Case118 load shares and signed Q/P ratios distribute
requested demand across 99 loads. Seeded siting and weights allocate 119
nondispatchable channels; 27 ideal batteries are added. Conventional real
capacity is resized from 6,515 to 5,000 MW, with study generator costs. The
actual sites, IDs, capacities, costs and array hashes are retained in
`experiments/case118_tracy_2021/stage_a/`. These are modeling choices, not asset
observations. The older annual synthetic Case118 scenario appears in preparation
as a **comparison**; it does not supply the PES hourly forcing.

The four distinct 24-hour windows were chosen around six-hour regimes using the
annual Tracy network-flow battery trajectory and endpoint proximity to 50% SoC.
Indices are zero-based, with the stop excluded:

| Scenario | Hours in the Tracy year | Initial → final battery energy |
| --- | --- | --- |
| Large surplus | [3308, 3332) | 50% → 50% |
| Large deficit | [1165, 1189) | 50% → 50% |
| Deficit depletion | [1165, 1189) | 60% → 25% |
| Surplus to deficit | [8580, 8604) | 50% → 50% |
| Deficit to surplus | [2439, 2463) | 50% → 50% |
| Forced shedding | [1165, 1189) | 50% → 50%; multiply requested P **and** Q by 7.5416905141946184 |

These are six **scenarios**, not six independent sampled days. The candidate
rankings are in `e3_selection/`; the underlying annual convex solves are in
`results/stage_c/`. The forced-shedding fixture binding is included separately.

## Verify, then reconstruct without solving

In the extracted directory, run `python3.11 verify.py` (Python 3.11+). This
checks SHA-256 for every payload file against `MANIFEST.json`. To rebuild data
and figures, install `uv` and the native IPOPT development library first (on
macOS: `brew install ipopt pkg-config`; see the bundled project README for
Linux). IPOPT is a package installation prerequisite even for the non-solving
Python readers. Then, starting beside this README:

```sh
cd cvxopf
uv sync --frozen --python 3.11 --extra dev --extra notebook
uv run --no-sync python ../verify.py --inputs
uv run --no-sync python -m experiments.case118_tracy_2021.select_e3 --output replay/selection
cmp replay/selection/selection.json experiments/case118_tracy_2021/e3_selection/selection.json
uv run --no-sync python -m experiments.pes2027.plot_pes_network --output replay/network
uv run --no-sync python -m experiments.pes2027.plot_pes_soc --output replay/soc
uv run --no-sync python -m experiments.pes2027.plot_pes_results --output replay/comparisons
uv run --no-sync python -m experiments.pes2027.plot_pes_rollout_soc --output replay/rollout_soc
uv run --no-sync python -m experiments.pes2027.analyze_rollout_controls --output replay/controls/distances.json
uv run --no-sync python ../verify.py --results replay
```

Use fresh output names on a second pass. The input check reads the original
CSV, independently audits the allocation, and compares the three arrays and
four device tables with the frozen Stage A evidence. The plotters above read
the joint archives, fixed-transfer outcomes and executed rollout trajectories;
this is different from simply redrawing a saved plot. Expect 24 joint records,
18 fixed-transfer attempts (15 accepted), and 24 completed rollout trajectories.
The three flagged convex forced-shedding donors keep their original qualifications.
The two rejected and one timed-out fixed transfers remain in the record; the
separate interval certificates, not these solver outcomes, establish incompatibility.
Use numerical data and scenario identities for comparison, not PDF file hashes.
The final command checks those scientific fields for exact equality against
the saved values; it does not require historical analysis paths or PDF
metadata to match. If a different native stack yields a discrepancy, inspect it
rather than silently relaxing the comparison.

## Repeat the optimizations (separate, optional)

The numerical runners require a clean Git checkout and record the local Git,
package, solver, and process-monitoring context. The archive does not contain
a portable authority to launch a new study. Do **not** resume the enclosed
author runs or rewrite their bindings. Use retained outputs as reference and
give every new run its own unused directory.

1. Clone [cvxopf](https://github.com/cvxgrp/cvxopf) into a **different**
   directory. From beside this README, the following example installs just
   the retained, normally ignored inputs/results into the clone; source code
   comes from Git. `verify.py --install-evidence` refuses conflicting files.

   ```sh
   git clone https://github.com/cvxgrp/cvxopf.git cvxopf-run
   git -C cvxopf-run switch --detach 2edc6d3bbeac85bd78f5bf6d9cdc387f851eb0f0
   python3.11 verify.py --install-evidence cvxopf-run
   ```

2. This starts at the actual **prepared E3** execution commit. The packaged
   figure-reader revision is separately identified in the manifest. The installer
   only copies paths ignored by that historical checkout so that future experiments
   do not contaminate its Git status. From the clone:

   ```sh
   uv sync --frozen --python 3.11 --extra dev --extra notebook
   git status --short
   uv run --no-sync python -m experiments.case118_tracy_2021.run_e3 --prepared \
     --commit 2edc6d3bbeac85bd78f5bf6d9cdc387f851eb0f0 --preflight \
     --output experiments/case118_tracy_2021/results/rerun_e3
   ```

   Stop if Git is not clean or preflight fails. **The historical E3/E3+ execution
   monitor is Mac-specific:** it checks process RSS, AC power via `pmset` and
   CPU/GPU temperature via `macmon`. On Linux or on a Mac without usable
   telemetry, the unmodified runner cannot launch: a reviewed equivalent
   monitor is needed. This does not affect input reconstruction or figure replay.
   The prepared binding records Python 3.11.15, CVXPY 1.9.3, CLARABEL 0.11.1 and IPOPT
   3.14.19 on Apple Silicon macOS. A lockfile does not pin the native IPOPT
   build or guarantee matching nonconvex iterates. Review budgets and past
   protocol revisions in the archive before launch. After local review, use
   the same command with `--authorize-execution` instead of `--preflight`.
3. For forced shedding, repeat that procedure at
   `13ad35bc5f0e9f8b5d20d54a55d2ab2cd7a26df8`, with `--plus` and a fresh
   `results/rerun_e3_plus` under the same experiment. After switching to
   each later SHA, rerun `python3.11 ../verify.py --install-evidence .` from
   the clone and sync its lockfile. See `E3_PREPARED_PROTOCOL.md` and `E3_PLUS_PROTOCOL.md`.
4. For fixed-power transfer at `4fbd85f3bd80a573edac4da284e325804f1d77a3`,
   use `experiments.tracy_battery_transfer.run --preflight --commit FULL_COMMIT`
   with a new `--output`; follow its `PROTOCOL.md` for launch. For the three-hour
   rollouts the original and continuation SHAs are
   `8267dbf857872e4b6adcbf6c84dc05f002d63f60` and
   `b260e354d6eb63dfa559af7b13996617e9cdcd7a`. Use the rollout README's
   `--preflight`, then `--prepare` with new budgets and a fresh reviewed
   authority before `--run`. Its original/continuation archives describe one
   study; the historical interruption is not a required experimental step.
5. **Choose the donors deliberately.** Downstream readers expect the original
   cohort paths and some exact original hashes. Repeating transfers/rollouts
   **from the bundled donors** is different from using your own newly computed
   donor schedules. The latter requires binding the new files, checking the
   same scientific admission rules and recording the changed source; it is
   not an automatic consequence of rerunning E3 in a fresh directory. Do not
   rename a new result over an original or disable a hash check to make it pass.

The retained references include the serial preparation comparison, and the
snapshot includes the paper's SOCP certificate evidence. Neither is an
additional day in the six-scenario comparison. Historical logs may contain
the original machine's paths; preserve their bytes as evidence.
