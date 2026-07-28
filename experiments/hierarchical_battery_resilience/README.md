# Hierarchical battery-resilience experiment

**Status:** stub — protocol design not yet frozen

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
    manual_runner.py
    runner.py
    analysis.py
    reproduce.py
    experiment_log.md
    results/
        .gitignore
```

Only the documentation and results-directory stub exist initially. Python
modules should be created when their corresponding phase begins.

## Immediate next step

Write `protocol.md` before implementing the manual runner. It must freeze:

- scenario and device system;
- outer and inner horizons;
- post-step SoC indexing;
- state passed into and out of each AC window;
- terminal-policy variants;
- replanning cadence;
- failure/fallback behavior;
- recorded metrics; and
- pre/post-implementation equivalence tolerances.

The endpoint-fixed DC subsection and AC realization study in
`experiments/battery_terminal` is prior evidence, not a substitute for the
closed-loop companion experiment.
