# Hierarchical battery-resilience experiment

**Status:** S0–S7 complete; public API reproduces the reviewed references

This is the companion experiment for
[`plans/milestone-17-hierarchical-dc-ac.md`](../../plans/milestone-17-hierarchical-dc-ac.md).

It has two phases:

1. **Before M17 implementation:** manually orchestrate existing long-horizon
   `lossy_dc` and short-window AC builders. This becomes the executable
   specification for indexing, SoC signposts, realized-state feedback,
   replanning, fallback behavior, and diagnostics.
2. **After M17 implementation:** run the same frozen physical scenario and
   protocol through the public M17 API and compare it window by window with
   the manual reference.

The experiment tests hierarchical orchestration at a scientifically meaningful
scale. It is larger than a unit or integration test.

## Planned layout

```text
experiments/hierarchical_battery_resilience/
    README.md
    protocol.md
    S0_REPORT.md
    scenario.py
    prepare_scenario.py
    prepared_scenario/
        manifest.json
        load_p.csv
        load_q.csv
        nondispatchable.csv
    manual_runner.py
    runner.py
    analysis.py
    reproduce.py
    experiment_log.md
    results/
        .gitignore
```

The frozen scenario loader, prepared arrays, auditable manual reference
runner, causal-recovery study, and public hierarchical controller are
implemented. S7 establishes window-by-window public/reference equivalence for
all four S3 policies and the S3b causal-recovery policy.

`load_frozen_scenario()` is the canonical build-ready boundary: it verifies
the artifacts and current case9 network and materializes `OPFOptions`, every
typed device fleet, aligned trajectories, and the frozen controller
configuration. Later runners do not interpret manifest field names directly.

## Result and next step

The [S7 report](S7_REPORT.md) records zero failed comparisons across the five
public/reference cases. The two frozen policies complete, the two replanned
`flat_only` policies reproduce their reviewed failure paths, and hard
`shifted_with_recovery` reproduces the complete 96-interval S3b trajectory and
all 864 registered attempt slots. The next step is M17-S8 documentation and
milestone handoff. Machine-readable S7 provenance is preserved in
[`S7_RESULTS_METADATA.json`](S7_RESULTS_METADATA.json); complete detailed
artifacts remain local and ignored.

The normative scenario uses checked-in Tracy-derived prepared arrays. The
source dataset was assembled from public sources, and the project owner has
confirmed authority to republish the derived inputs. The prepared arrays carry
timestamps, transformation provenance, and hashes; the large raw source
file remains unnecessary for reproduction.

The baseline uses separate hard and soft runs with no automatic fallback.
Every storage unit participating in M17 provides an explicit unique ID.

The standard outer policy is the hard energy-neutral equality `e_H = e_0` for
every storage device. Every shortened replan retains that same absolute target
at the original global boundary. The frozen initial and terminal value is
500 MWh, or 50% of capacity.

The initial outer-policy comparison is fixed as `frozen` versus
`replan_every_step`. The former is the less computationally intensive
open-loop benchmark; the latter is the feedback, MPC-like policy that rebuilds
the remaining-horizon DC plan from each realized AC state. Periodic replanning
is deferred.

The endpoint-fixed DC subsection and AC realization study in
`experiments/battery_terminal` is prior evidence, not a substitute for the
closed-loop companion experiment.

## Reproduce S3

```bash
uv run python -m experiments.hierarchical_battery_resilience.reproduce
```

Use `--resume` after interruption to retain complete readable artifacts and
rerun missing or incomplete cases. Resume first verifies the scenario and
software context. Results are written under the ignored `results/s3_manual`
directory; `analysis.py` validates their recorded hashes before loading
summary tables.

## Reproduce S7

After the reviewed S7 runner is committed and the worktree is clean:

```bash
uv run python -m experiments.hierarchical_battery_resilience.s7_equivalence
```

The runner verifies the frozen S3/S3b hashes, executes all five policies
through `solve_hierarchical_opf()`, and writes the detailed ignored comparison
artifact under `results/s7_public_equivalence`. It refuses a dirty worktree or
a nonempty destination and never resumes a partial equivalence study.
