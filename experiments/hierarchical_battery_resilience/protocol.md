# Hierarchical battery-resilience protocol

**Status:** to be designed and frozen before the M17 public implementation

This file will define the experimental protocol shared by:

- the Phase-1 manual orchestration runner; and
- the Phase-2 public M17 API runner.

The protocol must not be revised merely to make the implemented controller
look better. Material revisions should be recorded in `experiment_log.md`
with their scientific or engineering rationale.

## Decisions pending

- Physical scenario and duration
- Device specification
- Outer DC horizon
- Inner AC window length
- SoC index-alignment example
- Hard and soft terminal-policy definitions
- First-action execution rule
- Outer replanning cadence
- AC infeasibility and hard-target fallback
- Solver and initialization policy
- Open-loop and closed-loop comparisons
- Metrics and result tables
- Numerical and scientific acceptance tolerances
