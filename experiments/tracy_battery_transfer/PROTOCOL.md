# Convex battery schedules on the AC network

This experiment asks whether storage operating schedules recommended by the
three convex models can be accommodated by the AC network, and how the remaining
dispatch changes. Neither the unrestricted AC estimate nor a transferred
estimate is treated as truth or a certified global optimum.

## Comparisons

Run one AC attempt for each of three donor models in each of the six retained
Tracy scenarios: SOCP, lossy DC, and copper plate, for 18 cases altogether.
The five ordinary scenarios and the forced-shedding scenario participate on
equal footing. Each schedule spans the original 24-hour input window.

Retained prepared E3 and E3+ archives supply schedules and reference estimates.
Their source commits are respectively `2edc6d3bbeac85bd78f5bf6d9cdc387f851eb0f0`
and `13ad35bc5f0e9f8b5d20d54a55d2ab2cd7a26df8`. Completed supervision,
manifest hashes, scenario identities, and battery order must agree before use.
The three owner-approved E3+ convex schedules retain their original numerical
flags; using them does not rewrite their historical classifications.

## Fixed and free decisions

Pin every battery's real power at every hour to its donor value with affine
equality constraints in MW, positive for discharge. Do not round, clip, repair,
or otherwise alter source schedules. Keep initial SoC, capacity, inverter
ratings, aging costs, input demand, renewables, and network limits unchanged.

Remove the hard terminal-SoC equality through the existing battery API, setting
`terminal_soc=None` and `terminal_constraint=None`. The fixed initial state and
the pinned power schedule already determine all subsequent states through the
unchanged ideal-storage dynamics. This avoids a redundant equality for every
battery. Preflight checks the implied trajectory and endpoint against the
original storage limits and target; the original endpoint remains in metadata
and the independent result audit, not as another optimization constraint.

Conventional generation, renewable dispatch, load shedding, all reactive
dispatch, voltage, angles, and network flows remain free within their original
feasible sets. No slack generator is silently added and no network balance is
relaxed. This is conditional AC dispatch, not replay of the donor's entire
real-power injection pattern.

## Preparation and initialization

Use production AC device normalization, exact fixed-coordinate removal,
cycling and shedding cost coordinates, and component-first objective assembly.
Keep prepared E3 AC solver settings unchanged. There is no hourly fallback,
initialization ladder, target-free solve, automatic repair, retry, or resume.

Initialize from the retained matched AC estimate. Replace only battery real
power and the SoC trajectory implied by the source schedule. All other starting
values remain those of the matched AC estimate. Retain the complete physical
start and canonical handoff; independently verify native/restored coordinates.

## Numerical evidence and interpretation

Retain request, launch, phase, monitoring, resource samples, worker log, full
start, canonical start, native evidence, preparation evidence, result, completion
manifest, and finalized supervision. The read-only status command reconstructs
the pinned graph and independently restores and audits completed native results.
Archives without finalized supervision are unfinished and cannot be accepted.

Require full native convergence and the existing AC physical and accounting
checks. Also check every hourly battery pin within the existing `1e-4 MW`
power tolerance, the implied SoC trajectory within `1e-4 MWh`, and the original
endpoint within `1e-3 MWh`. These are numerical validation tolerances, not bands
in the optimization problem. Cycling epigraph gaps remain warnings only. The
forced case retains its input-only unserved-energy lower-bound check.

Report native numerical rejection, audit failure, timeout, and infrastructure
failure separately. A failed IPOPT solve is not proof that the schedule is
incompatible with AC. Any successful audit supplies a compatible operating
estimate, not a certificate of global optimality.

For eligible completed results, report transfer minus unrestricted AC changes
in served energy, ENS, conventional generation, curtailment, physical network
absorption, physical cost, and each cost component. Battery throughput is fixed
by the donor schedule. A negative cost difference is allowed: it may reveal a
better local estimate, not a negative global cost of imposing constraints.
Preserve unsuccessful cases and unavailable comparisons rather than selecting
only favorable transfers.

## Execution limits and review checkpoint

The owner approved 18 launches, one per case, 3,600 supervised seconds per
attempt, 16,384 MiB sampled RSS, and 64,800 cumulative supervised worker-seconds.
Run workers sequentially with one numerical thread. Reuse the existing process
supervisor and require process monitoring, AC power, and valid thermal telemetry
before launch. Stop on monitoring or infrastructure failure; a timeout or native
rejection consumes its case and does not trigger a retry.

Execution requires a reviewed clean full commit and separate launch approval.
The fresh output directory is `results/transfer_001` inside this experiment.
Raw artifacts remain ignored. Earlier runs, E3, E3+, and Stage D are untouched.

Preflight performs graph construction and canonicalization without optimization
or creating a run directory:

```sh
uv run --extra dev python -m experiments.tracy_battery_transfer.run --preflight
```

After review, owner commit, and launch approval:

```sh
uv run --extra dev python -m experiments.tracy_battery_transfer.run \
  --authorize-execution --commit FULL_COMMIT_SHA
```

Inspect retained evidence without optimization or writes:

```sh
uv run --extra dev python -m experiments.tracy_battery_transfer.run --status
```

Run the non-solving regression checks:

```sh
uv run --extra dev python -m pytest -q experiments/tracy_battery_transfer/test_transfer.py
```
