# Time-varying generator prices

**Status:** scope agreed by the owner on 2026-11-13; public implementation
not started. This plan defines the next implementation slice.

## Purpose

Represent market-priced grid energy through `DispatchableGenerator`, using
the same cost assembly as ordinary generation. A household's grid connection,
for example, can supply power at an hourly purchase price while storage shifts
energy between intervals.

The residential battery study already implements this in its local CVXOPF
copy. Its `generator_prices.patch` and `test_generator_prices.py` are under
`/Users/bmeyers/Documents/enphase_data/studies/residential_battery/`. They are
reference material, not a public implementation or sufficient qualification
for every supported formulation. Preserve that copy and all study results.

## Agreed first slice

- Add optional `DispatchableGenerator.linear_cost_series`, default `None`,
  with one finite price per modeled interval in problem-time order.
- Prices have cost-per-energy units, normally $/MWh. They replace the affine
  marginal-cost coefficient, rather than adding a second purchase cost.
  Preserve the constant cost term.
- Initially support affine polynomial costs only, as in the study extension.
  Reject quadratic and piecewise-linear combinations when a series is supplied.
  Static generators retain all existing supported costs.
- Support standalone AC, SOCP, lossy DC and single-node DC, with single-step,
  stepwise multistep and vectorized multistep builds.
- Reuse the generator component and shared cost assembly. Do not add a grid
  device, a second pricing engine or formulation-specific tariff logic.
- Verify signed grid exchange, non-hourly intervals, reported costs, supported
  numerical preparation and unchanged static behavior.
- Reject hierarchical use of generators carrying a price series until
  price-window slicing and handoff are separately supported.

Tariff calendars, billing periods, baseline credits and monthly charges remain
application-level logic. Market clearing, new import/export operating rules,
and time-varying power limits are outside this slice.

## Public behavior

Append `linear_cost_series: tuple[float, ...] | None = None` to the dataclass
fields so existing positional arguments retain their meaning. Supply priced
generators through the builders' existing `generators=` argument; no new
builder argument or input DataFrame is needed.

For an opted-in generator with affine cost `(c0, c1)` and price series `price`,
the cost rate in interval `t` is:

```text
generator_rate[t] = c0 + price[t] * Pg_MW[t]
generator_cost = delta * sum_t(generator_rate[t])
```

The series replaces `c1`. With `cost_coeffs=None` or a constant-only tuple,
the omitted marginal coefficient is zero. The initial supported representation
uses at most two polynomial coefficients, matching the study extension;
three-coefficient tuples are rejected with a series even if their quadratic
coefficient is zero. This restriction does not apply to static generators.

For a single-step build, require a one-element series. For multistep builds,
require exactly `T` elements. Do not broadcast a scalar, cycle a short series,
truncate a long one or infer calendar alignment. Reject nonfinite values and
incorrect shapes before solving. Zero and negative finite prices are allowed;
generator power bounds and network/device constraints remain unchanged.

A signed generator uses the same price for positive and negative power.
That can represent symmetric import/export settlement, but does not by itself
represent different purchase and export tariffs. Examples must state this
distinction and use bounded exchange. Keep currency/kWh-to-currency/MWh
conversion explicit in any household example.

Snapshot caller-provided series during building, consistently with other
device data; later mutation must not silently change a built problem. Retain
the existing result schema, reporting the integrated series contribution in
`generator_cost` and the objective. MATPOWER `gen`/`gencost` tables remain
static and cannot encode this series: do not imply that a round trip through
those tables preserves it.

Follow the study extension's explicit rejection in
`max_generation_marginal_cost()` when a series is present. Extending that
static diagnostic is not required for the first slice.

## Implementation approach

Keep validation and numeric price preparation in `generator.py`. Reuse the
existing polynomial cost helpers in `cost.py`, and let the generator adapter
provide the per-step or time-vectorized rate to shared assembly. Shared
assembly remains responsible for multiplying stage rates by `delta` once.
Do not reproduce generation costs in result extraction or SOCP diagnostics.

When every series is `None`, retain the existing static construction path,
including metadata and expression structure. For opted-in builds, prepare
prices once, preserve generator ordering in mixed static/priced fleets, and
use the same values for ordinary and prepared solves. Preserve the established
behavior of out-of-service generators and constant terms.

The controller must reject a supplied series before any planning or operating
solve, even when its length happens to match the first planning horizon. This
prevents accidental reuse or truncation as the horizon shrinks.

## Verification before public acceptance

| Check | Required coverage |
|---|---|
| Validation | Correct horizon length, finite numeric values, malformed arrays, single-step length one, unsupported cost types, and hierarchy rejection before solving. |
| Cost algebra | Prescribed positive/negative power and prices; replacement rather than addition of `c1`; retained `c0`; heterogeneous static/priced fleets; out-of-service behavior; `delta=0.25`, `1` and a duration above one hour. |
| Layout and model coverage | All four formulations in single-step, stepwise and vectorized builds; identical stage-rate accounting at assigned points and T=1 consistency. Check device DCP properties directly. |
| Public results | Small solved convex examples and a small AC fixture, using `build.solve()` and independent reconstruction of physical generation cost. Distinguish solver status from physical residual checks; no global AC optimality claim. |
| Preparation | Supported scaling/exact-fixed policies and AC hourly/component-first assembly preserve the price series, original-unit reporting and integrated objective. Unsupported combinations retain clear errors. |
| Compatibility | `None` leaves static graphs, metadata and result keys unchanged; input arrays/objects remain unmodified; existing static-cost and MATPOWER tests pass. |
| Documentation | A short bounded grid-pricing example explains units, sign conventions, series ordering, static serialization limits and unsupported hierarchy. Remove overly broad wording suggesting that market-priced energy is incompatible with the generator model. |

Reuse the current generator, component-assembly, numerical-preparation and
hierarchical test modules where appropriate. The study-local tests are useful
starting cases, not a replacement for public API regressions. Run targeted
tests, Ruff, configured mypy and the full `dev` + `notebook` regression suite
before proposing acceptance. These are library tests, not a new energy study.

## Checkpoint and exclusions

Review this plan with `cvxopf-review` before implementation. The implementation
will be a separate owner-authorized slice, followed by the same build-review
loop and a proposed commit message. Leave work unstaged and uncommitted unless
the owner explicitly requests otherwise.

This plan does not authorize new experiment studies, changes to historical
results, new solver policies, hierarchical pricing, tariff parsing or billing
optimization. It is separate from the completed lossy-storage work.
