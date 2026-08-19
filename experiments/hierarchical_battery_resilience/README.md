# Hierarchical battery-resilience experiment

**Status:** S0 characterization implemented and verified; review pending

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
    manual_runner.py
    runner.py
    analysis.py
    reproduce.py
    experiment_log.md
    results/
        .gitignore
```

Only the documentation and results-directory stub exist initially. Python
modules should be created when their corresponding phase begins; no manual
runner or public controller is implemented yet.

## Immediate next step

Review and freeze `protocol.md` before implementing the manual runner. The
current draft defines the state recurrence, study types, failure taxonomy,
recorded diagnostics, and reproducibility requirements. It still requires
selection of:

- scenario-specific horizons, soft weight, initialization, and tolerances
  after S0 characterization.

The normative scenario will use checked-in Tracy-derived prepared arrays. The
source dataset was assembled from public sources, and the project owner has
confirmed authority to republish the derived inputs. The prepared arrays will
carry timestamps, transformation provenance, and hashes; the large raw source
file remains unnecessary for reproduction.

The baseline uses separate hard and soft runs with no automatic fallback.
Stable storage identity is a prerequisite implementation slice after S0 and
before scenario freeze; every storage unit participating in M17 must provide
an explicit unique ID.

The standard outer policy is the hard energy-neutral equality `e_H = e_0` for
every storage device. Every shortened replan retains that same absolute target
at the original global boundary. The scenario will freeze the initial value;
50% of capacity is the provisional configuration.

The initial outer-policy comparison is fixed as `frozen` versus
`replan_every_step`. The former is the less computationally intensive
open-loop benchmark; the latter is the feedback, MPC-like policy that rebuilds
the remaining-horizon DC plan from each realized AC state. Periodic replanning
is deferred.

The endpoint-fixed DC subsection and AC realization study in
`experiments/battery_terminal` is prior evidence, not a substitute for the
closed-loop companion experiment.
