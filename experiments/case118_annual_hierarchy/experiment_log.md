# Experiment log — annual case118 hierarchy

## 2026-08-20 — planning restarted

The experiment branch was fast-forwarded to the completed M17 implementation
and its CI-portability and coverage follow-ups.

Initial case characterization from the checked-in `case118()` factory:

- 118 buses;
- 186 active branches;
- 54 generators;
- 99 buses with positive active demand;
- 4,242 MW base active demand and 1,438 MVAr base reactive demand;
- 9,966.2 MW aggregate generator `Pmax`; and
- all 186 branch `rateA` values equal to 9,900 MVA.

The last point means the unmodified case is useful for nonlinear-network and
scale behavior but not for a meaningful congestion claim.

The planning review then approved meaningful line limits in scope. The primary
recommendation is the complete published
`pglib_opf_case118_ieee` case from PGLib-OPF, not a rating-only transplant.
PGLib supplies reproducible engineering-model ratings while retaining a
coherent set of adjusted loads, generator capabilities, costs, and voltage
bounds. Its active-power-increase variant is reserved as an optional congested
stress sensitivity after the ordinary rated case is characterized.

The protocol must record that cvxopf does not implement the PGLib branch
angle-difference bounds. Using the case therefore does not imply exact
reproduction of the complete PGLib operating set or published benchmark
objective.

Review identified that repository MATPOWER case118 cannot serve as the
unlimited congestion control because it differs from PGLib in more than branch
ratings. The matched counterfactual is now generated from the converted PGLib
case by changing only `rateA` to zero. Repository case118 remains a separate
implementation/scale comparator. The wording was subsequently tightened:
lossy DC substitutes its finite `branch_limit_sentinel` for zero ratings while
AC omits them, so the control is “effectively unlimited” and must verify a
tenfold ex-post flow margin to the sentinel.

Review also identified a pre-execution architecture blocker. The public M17
API cannot checkpoint or expose per-window memory while running, and it retains
live builds until the complete result returns. A new P0 gate now requires an
experiment-owned streaming reference runner, built only from public OPF APIs,
with exact short-horizon equivalence to the public controller before any
week/month/year execution.

The P0 gate was expanded after review because nominal short runs might never
leave the shifted-primary path. It now requires deterministic public/streaming
equivalence across target-free and copied recovery, both perturbation families,
certified infeasibility, solver failure, unusable primals, and nine-slot
exhaustion, plus replay of the observed M17 S3b recovery event.

The causal-perturbation fixture is explicitly a later-window case following an
accepted executed interval, so it exercises the real immediately-preceding
source contract.

Two additional architectural limits were recorded before scenario work:

1. the annual frozen hierarchy still requires one 8,760-step case118 lossy-DC
   outer build and solve; and
2. the public M17 result retains live accepted AC builds, so an 8,760-window
   execution may become memory-bound even when individual windows solve.

The draft plan therefore uses 1-hour and 6-hour probes followed by day, week,
month, annual-outer, and annual-execution stages. The full-year run is
conditional on earlier resource and audit gates rather than predeclared as
inevitable.

## 2026-08-21 — S0 source pin and first operating-point probes

The official PGLib source was pinned at revision
`dc6be4b2f85ca0e776952ec22cbd4c22396ea5a3` and redistributed with its CC BY
4.0 license. A strict loader now verifies source-file, license, normalized
source-array, and converted-array hashes.

The first compatibility probe found that PGLib supplies the ten required
MATPOWER generator columns while cvxopf validates the extended 21-column
layout. The deterministic conversion preserves source columns 0–9 and pads
the unused optional columns with zero. Both formulations then build without
row reordering.

The rated one-hour AC operating point solved to `optimal` in about 6.1 seconds
and reached a branch terminal rating. Its matched `rateA`-only control solved
to `optimal_inaccurate` in about 7.2 seconds with a lower local objective. The
latter remains diagnostic until independent residual checks accept it.

The constant-input six-hour AC probe solved to `optimal` but required about
294.6 seconds, compared with 0.042 seconds for lossy DC. This is an early
throughput warning, not a reason to change the scaling ladder. Full values and
the remaining S0 gates are recorded in `S0_REPORT.md`.

The deterministic UTC annual profiles, electrical-distance medoids, and
eight-point pilot grid were then frozen before inspecting storage-coupled
dispatch. Storage buses are 41, 65, 89, and 105; wind and solar are placed at
105 and 65. The lowest pilot point passed both rated one-hour independent
audits. Its six-hour rated/control audits remain the next scientific gate.

Review found that the first profile hashes covered raw transcendental output
and that the preliminary AC audit omitted M17's negative-curtailment and
negative-branch-loss gates. Profile values are now rounded to nine decimal
places before use and hashing. The AC audit now requires the complete branch
and curtailment evidence and applies both nonnegativity residuals. Synthetic
drift tests prove that material violations are rejected.

The quantized one-hour AC probe passed the complete gate but reached a
different local objective from the earlier unquantized exploratory solve. The
unquantized value is explicitly superseded; no authoritative comparison will
mix the two input specifications.

## 2026-08-21 — first frozen six-hour pilot

The lowest grid point ran from clean commit `634dc1f`. All rated and matched
control AC/DC results passed the complete independent audit. Rated AC required
585 seconds and drove the process peak to approximately 14.5 GiB. The matched
unlimited AC solve required 117 seconds and was accepted as
`optimal_inaccurate` only after its residuals passed.

The rated network was thermally binding but its storage movement was only
floating-point noise. The unlimited AC case moved 6.001 MWh and curtailed
35.585 MWh. Because the predeclared word “nonzero” lacks a threshold and does
not explicitly identify the rated network, the lowest point was not selected
by literal comparison. S0 pauses for that protocol clarification before
running another expensive pilot point.

## 2026-08-21 — reviewed S0 protocol amendment

Review agreed that meaningful movement must occur in rated AC and must pass
both a tolerance/rating-scaled instantaneous gate and a capacity-scaled
throughput gate. Review also replaced the arbitrary midnight interval with one
common exogenously selected window across the grid.

The 15% renewable reference net load selected boundaries 3757–3763 with split
5, or `2025-06-06 13:00` through `18:00` UTC. The scoring formula, rounding,
tie breaking, result hash, and strict movement comparisons were committed
before replacement execution. The midnight artifact remains separate and is
not combined with the amended run.

## 2026-08-21 — amended-window pilot selected

The replacement run executed from clean commit `578b270`. All four solves
passed the complete audits. Rated AC moved storage by 205.946 MW at maximum
and 465.760 MWh of throughput, decisively exceeding the committed 0.206 MW and
1.084 MWh gates. The lowest grid point is selected without running higher
penetrations or capacities.

The computational result is equally important: rated and unlimited AC solve
calls took 2,068.6 and 1,326.7 seconds, and process peak RSS reached 14.66 GiB.
The two convex solves each took roughly 0.06 seconds. S1 must therefore use a
hard direct-AC stopping budget and cannot extrapolate the midnight runtime.

## 2026-08-21 — S0 closure and S1 authorization

The repository `case118()` was characterized separately. Although its basic
dimensions and aggregate demand match PGLib, its generator capacity, costs,
bus data, and branch data differ materially; all branch ratings are 9,900 MVA.
It remains a scale comparator and is never used as the rating counterfactual.

S1 is limited to 16 GiB process RSS, 45 minutes per AC solve, and two hours
overall, with child-process RSS sampling at least once per second. Direct
24-hour AC is not authorized by the S0 resource gate. Twenty-four-hour lossy
DC and bounded AC/hierarchical measurements remain authorized. S0 is complete.

## 2026-08-21 — S1 completed

Both 24-hour lossy-DC outer plans solved and passed independent audits in less
than 0.35 seconds. Their selected six-hour AC endpoint realizations also
passed, taking 21.0 minutes rated and 19.3 minutes effectively unlimited. Peak
sampled worker RSS was 10.1 and 10.7 GiB. Direct 24-hour AC remained
unconstructed and classified `not_authorized_by_s0_resource_gate`.

Over the matched six-hour interval, the unlimited lossy-DC optimum selected
only $$1.02\times10^{-6}$$ MWh of storage throughput while its AC endpoint
optimum selected 478.285 MWh. The rated values were 2.353 and 468.220 MWh.
These are economically selected optima; S1 does not establish that AC
feasibility required the cycling. The rated outer's 151.748 MWh full-day
throughput remains a separate daily summary. This is endpoint-realization
evidence, not a sequential operating total. S1 passes and P0 is next.

## 2026-08-22 — S2 reached its resource boundary and the week completed by continuation

The authoritative 16 GiB invocation completed and durably archived 128 of 168
intervals before the supervisor observed 19,220.4 MiB RSS. The supervisor
classified the event as `resource_limit`, terminated without advancing an
incomplete interval, and preserved interval 128 as a verified restart
boundary. This is a successful exercise of the declared protection path, not
a solver failure or infeasibility result.

An explicitly labeled exploratory continuation raised the process boundary to
24 GiB, resumed the same causal trajectory at interval 128, and completed the
remaining 40 intervals in 2,142.2 seconds. The fresh worker began at 182.6 MiB
RSS, its first resumed window peaked at 10,431.3 MiB, and its maximum recorded
safe-boundary RSS was 17,874.9 MiB. The memory reclaimed by restarting is
positive resilience evidence from a real resource event and motivates planned
worker recycling. It does not yet identify the retaining subsystem or an
optimal recycling interval.

Independent reconstruction accepted all 168 controlling attempts. All 167
applicable shifted starts succeeded without recovery. Maximum SoC recurrence,
terminal, voltage, and thermal residuals were respectively
$$7.65\times10^{-12}$$ MWh, $$5.68\times10^{-14}$$ MWh, zero pu, and
$$2.78\times10^{-7}$$ MVA; fixed-load service error was zero. The restart
preserved the physical state and causal-controller chains.

The continuation worker's `eligible_for_advancement: true` is local to its
numerical and provenance checks. It does not turn the combined run into an
uninterrupted pass of the frozen 16 GiB condition. S2 is therefore complete as
a resource-boundary stage, with a complete scientific trajectory obtained
through an exploratory continuation.

Invocation 0 began from clean commit `3cd4229`. An untracked scaling-notes
file appeared during execution, so its end context was dirty even though the
executable fingerprint, commit, scenario, policy, and solve configuration did
not change. The continuation ran with matching clean execution provenance.
`S2_REPORT.md` gives the bounded interpretation, and
`S2_RESULTS_METADATA.json` tracks the compact metrics, restart boundary,
provenance, and artifact integrity identifiers.
