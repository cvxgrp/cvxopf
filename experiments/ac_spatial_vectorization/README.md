# Matched AC branch-terminal vectorization benchmark

The benchmark asks how batching the four branch-terminal equations changes
model setup and time to an accepted AC solution. The comparison is the
[terminal-definition slice](../../plans/large-case-ac-spatial-vectorization.md)
only: baseline `812aa7d62` versus batched `6ab39dac7`. No changes to thermal
limits, nodal balances, numerical preparation, or physical inputs between
the two arms of a case.

## Cases

| Case | Horizon | Source hours |
| --- | ---: | --- |
| Case118/Tracy large surplus | 24 | [3308, 3332) |
| Case118/Tracy large deficit | 24 | [1165, 1189) |
| Case118/Tracy deficit depletion | 24 | [1165, 1189) |
| Case118/Tracy surplus to deficit | 24 | [8580, 8604) |
| Case118/Tracy deficit to surplus | 24 | [2439, 2463) |
| Case118/Tracy forced shedding | 24 | [1165, 1189), with the published demand multiplier |
| PGLib 500-bus GOC, typical operations | 1 | Published snapshot |

The six Tracy cases are the full joint AC problems in [PES2027 Appendix A1](../pes2027/APPENDIX.md#a1-data-and-configuration).
Use the original storage endpoints and hard terminal constraints, load and
availability series, network, and costs. Use vectorized time assembly and the
prepared E3/E3+ primary `component_first` construction on both arms. This is
not a replay of whichever historical attempt won: no old primals, donor
schedules, target-free rungs, or recovery ladder enter the timing comparison.

The [PGLib snapshot](https://github.com/power-grid-lib/pglib-opf/blob/dc6be4b2f85ca0e776952ec22cbd4c22396ea5a3/pglib_opf_case500_goc.m)
replaces the unidentified smoke case. Use stock single-step CVXOPF AC on
the published tables, with no added storage, demand scaling, or time series.
The input reader pads only the unused generator columns required by CVXOPF.
This compares two CVXOPF expressions of the same model, not CVXOPF's agreement
with PGLib's benchmark objective or every MATPOWER option. No 500-bus
multistep case is planned for this pass.

## Check the inputs without solving

Use the project environment with the `dev` extra. Install the Tracy CSV as
described in the [Tracy data guide](../pes2027/TRACY_DATA_README.md). E3+ also
requires its pinned input binding at
`experiments/objective_assembly_qualification/results/qualification_002/binding.json`
(see the [reproduction input inventory](../pes2027/REPRODUCTION_INPUTS.md)).
No saved joint solutions or annual DC outputs are needed: this benchmark uses
the already selected windows and does not rerun window selection.

From the repository root:

```sh
root=experiments/ac_spatial_vectorization/results/inputs
mkdir -p "$root"
if [ ! -e "$root/pglib_opf_case500_goc.m" ]; then
  curl --fail --silent --show-error --location --remove-on-error \
    https://raw.githubusercontent.com/power-grid-lib/pglib-opf/dc6be4b2f85ca0e776952ec22cbd4c22396ea5a3/pglib_opf_case500_goc.m \
    -o "$root/pglib_opf_case500_goc.m"
fi
uv run --extra dev python -m experiments.ac_spatial_vectorization.preflight
```

The checker verifies the pinned bytes, resolves and hashes the public build
inputs, and builds the seven graphs. It prints one JSON record per case with
the modeled scalar counts. It does not compile, start a numerical worker,
write a run binding, or launch IPOPT. `--case500` can point to an existing
copy of the pinned file.

In the local preflight, all seven cases had matching input hashes and modeled
scalar counts with the baseline and batched terminal builders. On the 500-bus
snapshot, the baseline warned about subexpression counts for all four terminal
equations (constraints #5–8); the batched builder did not. This check did not
measure compilation or solve time.

## How to run the comparison once approved

1. Freeze both revision identities, the per-case input and named starting
   values, effective preparation/solver settings, environment, acceptance
   tolerances, and common worker budgets. Use the input-only primary starts
   for the Tracy cases and a stock start for the snapshot; verify the actual
   starts by physical coordinates, not by position in the canonical vector.
   Proposed ceilings: 16 GiB RSS and 3,600 seconds per whole worker, with
   an explicit 3,000 IPOPT iteration limit. Use one numerical thread and no
   concurrent workers. Agree on these settings before binding.
2. Run three fresh build-only pairs per case, alternating order. Record
   construction, DNLP reductions, and derivative setup as separate clocks;
   report peak worker RSS, warnings by constraint role, and scalar/derivative
   counts. Check that both arms built the same mathematical case. If a phase
   cannot be timed without a solve, mark it unmeasured.
3. Run one fresh solve per revision per case initially, balancing which arm
   runs first across the six Tracy cases. Use `build.solve()` with the same
   effective settings within a pair. The primary time is cold wall time from
   construction through extraction. Capture the actual start during this
   solve; do not compile ahead of time to check it. Record IPOPT time and
   iterations from the native log and record peak RSS separately. If a native
   statistic is unavailable (for example, after interruption), leave it
   missing. Any timer around the CVXPY adapter is a separate diagnostic: it
   can include derivative setup and is not an IPOPT time.

### Accepting and comparing solutions

For Tracy, reuse the E3/E3+ audits in original units and reconstruct the
objective independently. For the 500-bus snapshot, use the retained `Vm`,
`Va_deg`, `Pg`, and `Qg` and the pinned case tables to independently recompute
P/Q nodal balances (including shunts), both signed terminal powers and thermal
limits, voltage and generator bounds, the modeled reference/setpoint rules,
and the original generation cost. Audit the model CVXOPF actually builds;
the PGLib branch angle-difference limits are not enforced by this builder.
Agree on numerical tolerances before launch.

Report objective and dispatch deltas and the worst physical residuals. Show
timings for failed and censored attempts separately; compute speedup ratios
only for pairs with two accepted solutions. A single solve pair describes
those runs, not the variance of solve time; consider repeats if the result is
close or erratic.

### Stops and resume

Build the numerical runner separately. Reuse the serial supervision and
durable publication/reconciliation patterns from the matched numerical
preparation runner, without carrying over its scientific settings. Use one
stable work ID per case, phase, repeat and revision; retain attempts, logs,
measured effort and terminal dispositions in this experiment's ignored
`results/` directory. A requested stop leaves the interrupted unit pending
at the same start. Loss of required resource monitoring stops the run for
review and is recorded as a monitoring failure, not an observed RSS breach.
Wall timeouts and RSS exits are terminal censored attempts with distinct
reasons. An authorized same-settings resume reuses completed records and
does not repeat terminal attempts, including numerical rejection. Before
launch, use non-solving workers to rehearse stop/resume, publication recovery,
and resuming after an RSS exit without retrying that work unit.

Review the build-only profiles before deciding whether to batch thermal
limits. Nothing in this protocol authorizes numerical execution.
