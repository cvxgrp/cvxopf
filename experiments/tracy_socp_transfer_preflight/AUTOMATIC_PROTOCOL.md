# Automatic loop certificate preflight

Report-only extension of the frozen named-loop checkpoint. No OPF solve,
retry, repair, acceptance mutation, tolerance change, or historical overwrite.
The original named checker and evidence remain intact.

## Discovery and coverage

Enumerate deterministic simple undirected cycles of lengths 3–4 by default
(configurable maximum 3–6). This is complete within the configured length
range, not a claim about longer cycles or all infeasibility mechanisms.
Parallel cycle edges, non-unit taps, phase shifts, nonpassive/large-charging
geometry, missing positive ratings, and large/nonmonotone thermal angle arcs
are explicitly unsupported. Missing explicit generator/load inputs, HVDC,
or disabled branch limits yield unresolved coverage. There is no bus-ID list.

For every contiguous cycle arc, consider its bus set both alone and augmented
with every outside connected component attached to exactly one cycle vertex.
For each directed cycle edge leaving that set, derive the necessary export:

`P_edge >= sum(P_device_min) - sum(Gs*V²_max) - sum(internal_loss_max)
           - sum(other_boundary_thermal_ratings)`.

Signed shunts and generator/load bounds are included. All battery real-power
pins are used unchanged. Device aggregation is directed Decimal arithmetic.
Internal loss uses passive series-current bounds from both terminal ratings;
true single-branch leaves additionally use conservative P/Q injection boxes.
All supported cuts compete, and the greatest justified lower bound is used.
Unbounded cuts are omitted, not filled with guessed values.

## Exclusion

For a supported loop-hour with at least one positive necessary export, bound
each principal edge angle using both terminal apparent-power circles and the
forward/reverse necessary real-power exports. Thermal current bounds establish
a small principal arc and monotonicity of the real-power inverse. Sum the angle
intervals around the loop. Exclude a voltage box only for an empty edge-angle
intersection or if its closure interval excludes every integer multiple of
2π. π, inverse trigonometric bounds, and interval operations reuse the corrected
36-digit directed Decimal primitives; no point samples or floating-point angle
residuals serve as proof. Bisect voltage boxes conservatively, at most 64 visits
per triggered loop-hour. Store all terminal excluded boxes and cut terms for a
certificate. Any unexcluded box means unresolved, including budget exhaustion.
The no-export screen skips work; it does not establish feasibility.

The necessary-condition calculation may enlarge device operating sets and
ignore temporal coupling to be conservative. It never changes the actual AC
model, pinned powers, SoC constraints, native solver policy, or audit gates.

## Back-test and provenance

Run the existing independent manifest/resource/compatibility replay for all
18 retained transfers without optimization. Scan all 432 donor hours and the
144 hours of six retained accepted AC controls. A certificate contradicting
an independently audited accepted witness is a fatal analysis error; never
relabel the witness or weaken gates. Retain native rejection and timeout labels
separately from certificates. Hash historical inventories and frozen analysis
checkpoints before/after; print new evidence only to stdout.

This is retrospective consistency testing on known data, not holdout predictive
validation, a complete infeasibility oracle, or an optimality certificate.

Command:

```bash
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --extra dev python \
  -m experiments.tracy_socp_transfer_preflight.automatic --backtest
```
