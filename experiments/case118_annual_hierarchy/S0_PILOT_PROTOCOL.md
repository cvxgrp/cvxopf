# S0 pilot protocol

This protocol freezes exploratory inputs before any storage-coupled OPF result
is inspected. Pilot outcomes may select one already declared point for the
authoritative scenario; they may not be used to invent a new point without a
new reviewed pilot protocol.

## Deterministic year

- 8,760 hourly intervals from `2025-01-01 00:00 UTC` through
  `2025-12-31 23:00 UTC`.
- The analytical load, wind, and solar formulas live in `scenario.py`; their
  outputs are rounded to nine decimal places before optimization and hashing.
- System load has mean multiplier 1.0 and every bus retains its source active
  and reactive demand proportions.
- Wind and solar profiles contain no random draws. Their prepared numeric
  hashes are frozen in focused tests.

## Placement

Active branches receive weight $$\max(|x|,10^{-6})$$ pu. The connected
undirected all-pairs distance matrix feeds deterministic, load-weighted
four-medoids clustering with external-bus tie breaking. The resulting storage
buses are 41, 65, 89, and 105.

Solar is placed at bus 65, the medoid of the cluster with the greatest base
apparent demand. Wind is placed at bus 105, the storage medoid electrically
farthest from the solar medoid. These choices precede dispatch results.

## Predeclared sizing grid

The eight pilot points are the Cartesian product:

- annual available renewable energy share: 15% or 30% of annual active-load
  energy;
- aggregate storage apparent-power rating: 5% or 10% of synthetic-year peak
  active load; and
- storage duration: 4 or 8 hours.

Half of each renewable-energy target is assigned to wind and half to solar;
nameplate capacities follow from the frozen profile capacity factors. Storage
power is divided among the four medoid clusters in proportion to cluster base
active demand. Energy capacity is rating times duration.

Every storage device starts at 50% SoC, has an equality target at the same SoC,
and uses a cycling weight of 1 objective unit/MWh. Loads are fixed and
nonsheddable. A no-renewable, no-storage source operating point remains the
network/conversion control but is not part of the eight-point sizing grid.

The S0 pilot uses the first annual interval for the one-hour gate and annual
intervals 0–5 (`2025-01-01 00:00` through `05:00` UTC) for the six-hour gate.
This window choice is fixed before pilot dispatch and is not selected for a
favorable renewable or congestion outcome.

## Pilot selection rule

The authoritative point should be the lowest-renewable, lowest-storage point
that passes all rated-network one- and six-hour scientific audits while
showing nonzero renewable output and nonzero storage movement in at least one
six-hour probe. If no point qualifies, S0 reports that result and returns for
protocol review; it does not expand the grid silently.

Passing the short pilot does not establish annual feasibility. Its only role
is to choose one predeclared, nontrivial scenario for the scaling ladder.
