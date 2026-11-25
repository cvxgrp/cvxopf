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
   can include derivative setup and is not an IPOPT time. Retain the native
   options as applied by the adapter, separate from the explicit input options.

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
only for pairs with two accepted solutions and matching verified physical
starts and native options. A single solve pair describes
those runs, not the variance of solve time; consider repeats if the result is
close or erratic.

### Stops and resume

The serial runner retains a separate work ID for each case, phase, repeat,
and revision. Attempts, logs, sampled RSS, publication receipts, and outcomes
live under this experiment's ignored `results/` directory. A requested stop
leaves the interrupted unit pending at the same start. Loss of required
resource monitoring stops the run for review, not as an observed RSS breach.
Wall timeouts and RSS exits are terminal censored attempts with distinct
reasons. An exit must be observed before the wall deadline to count as on
time. An authorized same-settings resume reuses completed records and
does not repeat terminal attempts, including numerical rejection. Before
launch, use non-solving workers to rehearse stop/resume, publication recovery,
and resuming after an RSS exit without retrying that work unit.

## Commands

From the repository root, with the input checks above complete:

```sh
bench() { uv run --no-sync --extra dev python -m experiments.ac_spatial_vectorization.run "$@"; }
study=experiments/ac_spatial_vectorization/results/benchmark_001
bench bind "$study" --settings /path/to/approved-settings.json
bench status "$study"
```

`bind` builds cold graphs to check the named starts, but does not compile or
solve. It prints the binding SHA-256 and saves the original case tables,
settings, and two Git source snapshots in the new run directory. Pass
`--case500 PATH` to use a different copy of the same pinned bytes. The
[example settings](settings.example.json) are proposals, not approval. The
snapshot tolerances are in MW/MVAr/MVA (`power`), per unit (`voltage`), degrees
(`angle`), and objective units (`cost_abs` plus `cost_rel` times the magnitude
of the recomputed cost). The Tracy audits keep their existing tolerances.

After separate execution approval for the printed SHA-256:

```sh
bench start "$study" --authorize-execution APPROVED_BINDING_SHA256
# From another terminal when needed:
bench status "$study"
bench stop "$study"
# After the old supervisor and its child have exited, when resume is approved:
bench resume "$study" --authorize-execution APPROVED_BINDING_SHA256
bench report "$study"
```

Start and resume verify the bound execution dependencies. Execution helpers
are checked again before each launch, while report code is separate and can
change without invalidating the binding. The worker checks its AC assembly
source, the loaded IPOPT library, and only its assigned fixture. A completed
solve retains both the actual handoff and a verification of that start in
physical coordinates, including fixed-coordinate and cost-scaling maps.
A fresh process is used for every arm. Resume acknowledges only stop requests
already present when it starts. A stop does not discard an in-budget worker
that has already exited, and a source check that refuses launch does not
reserve or charge worker time. Resume honors completed work even if the
progress file was not updated. A worker-only
archive without final supervision is retained but not counted as complete;
if the supervisor lost the end of an attempt, its effort is reported as a
ceiling charge, not a measured duration. Changes to settings or relevant
source require a new binding. Status and report do not compile, solve, or
write to the run. They read hash-checked worker audit receipts, not fresh
independent audits. Peak RSS is sampled, not a continuous high-water mark;
thermal telemetry is not collected by this runner.

For the non-solving rehearsals, run:

```sh
uv run --no-sync --extra dev python -m pytest -q experiments/ac_spatial_vectorization
```

Review the build-only profiles before deciding whether to batch thermal
limits. Nothing in this protocol authorizes numerical execution.
