# Parallel branches and fixed transformer certificate extension

Report-only successor to the immutable automatic-loop checkpoint. No OPF
solve, retry, pin/SoC/constraint/tolerance change, or historical overwrite.
Reuse v1 topology discovery, device boxes, cuts, interval primitives, and
bounded voltage partitioning. Add explicit branch identities and transformer
coordinates. The old source and selected evidence are preserved, not replaced.

## Transformer coordinates and bounds

For MATPOWER from-side tap `t = tau exp(j phi)`, define `U = V_from/t` and
`W = V_to`. The branch is the same passive pi line between U and W. Its
electrical angle is `theta_from - theta_to - phi`, and its internal sending
magnitude is `|V_from|/tau`. Terminal apparent powers are exactly
`S_from = base U conj(y(U-W)+j c U)` and
`S_to = base W conj(y(W-U)+j c W)`; they are the physical terminal powers,
not powers with an additional tap scaling. Thus the existing two-terminal
circle bounds apply with the transformed voltage box and unchanged ratings.
Add phi to recover a physical bus-angle interval; traversal reversal changes
the phase-shift sign. Normalize input degrees modulo 360 exactly before using
the directed pi enclosure. Taps must be fixed positive, with MATPOWER zero
meaning unity. Invalid/nonfinite/negative taps remain unsupported.

Series current uses both terminal ratings divided by their respective internal
voltage minima, plus charging current. Real loss remains `base*r*|I_series|²`.
Leaf-terminal power bounds use physical bus injections/shunts but internal
voltage for current conversion. No device or actual OPF equation is altered.

## Parallel corridors

Keep every active branch row, its own transformer data, both ratings and loss.
For a cut crossing a parallel corridor, first bound its aggregate directed
real export, excluding all corridor members from other-boundary ratings.
For member e, the justified individual lower bound is
`aggregate_min - sum(other corridor members' ratings)`. Never assign the entire
aggregate requirement to each member, split flow proportionally, or substitute
an equivalent line. Internal cut losses sum every physical branch separately.

All members connect the same buses, so intersect their physical bus-angle
intervals modulo 2pi. Align every integer lift that can overlap an anchor
interval; a hull across multiple surviving pieces is conservative. Empty
circular intersection excludes the voltage box. Sum the resulting corridor
intervals around each simple bus cycle and apply the existing winding check.
Also add one two-bus corridor check for every parallel pair; it includes all
members and therefore checks every two-branch cycle without combinatorial
duplication. Do not confuse these corridor groups with distinct edge-resolved
cycle counts.

Check one geometry-only root box for every supported loop, including loops
with no positive export, so a phase-shift contradiction is not screened out.
Cache this static root check. Only a positive necessary aggregate export
triggers the subsequent 64-visit dispatch-dependent partition. Unsupported
geometry, absence of a trigger, and budget exhaustion remain unresolved.

## Verification and scope

Independent direct complex-admittance fixtures must enclose both terminal
flows, transformed angle bounds and losses, with nonunit taps, nonzero signed
shifts, traversal reversal, unequal/reversed parallel branches and angle wraps.
Test a feasible parallel-transformer witness and synthetic contradictory
phase shifts, without solving. Re-back-test all 18 retained schedules and six
accepted controls using existing independent manifest/resource/compatibility
replay. Any contradiction with an accepted witness is fatal to the analysis;
never relabel the witness. Hash historical and v1 checkpoint bytes before/after.

Passive, positive-rated, modest-charging, small-electrical-angle assumptions
remain. HVDC, unrated branches, and longer cycles outside the configured length
remain outside this necessary-condition method. No certificate does not imply
feasibility; retrospective consistency is not holdout prediction or optimality.

```bash
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --extra dev python \
  -m experiments.tracy_socp_transfer_preflight.multigraph --backtest
```
