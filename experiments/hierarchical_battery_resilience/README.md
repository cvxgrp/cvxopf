# Hierarchical battery-resilience experiment

**Status:** S0, P1, and S1 complete; manual reference runner is next

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

The frozen scenario loader and prepared arrays are implemented. No manual
runner or public controller is implemented yet.

`load_frozen_scenario()` is the canonical build-ready boundary: it verifies
the artifacts and current case9 network and materializes `OPFOptions`, every
typed device fleet, aligned trajectories, and the frozen controller
configuration. Later runners do not interpret manifest field names directly.

## Immediate next step

Implement the auditable manual reference runner against the frozen protocol.

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
