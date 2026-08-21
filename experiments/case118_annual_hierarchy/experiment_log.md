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
