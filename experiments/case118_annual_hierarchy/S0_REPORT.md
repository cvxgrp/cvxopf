# S0 report — source and static operating-point characterization

**Status:** Complete

## Reproducible source boundary

The primary case is the unmodified PGLib-OPF
`pglib_opf_case118_ieee.m` source at revision
`dc6be4b2f85ca0e776952ec22cbd4c22396ea5a3`. The checked-in source,
CC BY 4.0 license, manifest, normalized source-array hashes, and converted-array
hashes are verified whenever the loader runs.

PGLib supplies the ten required MATPOWER generator columns. cvxopf's case
validator requires the extended 21-column layout. The deterministic conversion
preserves columns 0–9 and fills optional columns 10–20 with zero. It changes no
other numeric case value.

The converted case contains:

- 118 buses, 54 active generators, and 186 active branches;
- 99 buses with nonzero demand, totaling 4,242 MW and 1,438 MVAr;
- 6,515 MW aggregate generator maximum active power;
- 14 nonzero bus-susceptance shunts;
- 9 fixed nonunity transformer taps and no phase shifts;
- one reference bus, external bus 69;
- voltage bounds of 0.94–1.06 pu; and
- `rateA` values from 72 to 7,218 MVA, with a median of 153 MVA.

All 186 source branches carry nondefault angle-difference fields. These values
remain in the converted branch array but are not modeled by cvxopf.

The first-class conversion creates 99 fixed, nonsheddable `Load` records in
source bus-table order. IDs are `load_bus_<external bus number>`.

## Frozen pilot construction

The S0 pilot protocol was frozen before storage-coupled dispatch results. It
defines a UTC 2025 synthetic year with 8,760 analytical hourly values. The
load multiplier ranges from 0.67695 to 1.27758 with mean 1.0. Wind and solar
capacity factors average 0.43032 and 0.21226. All three channels are rounded
to nine decimal places before use and hashing, avoiding dependence on the last
bits of platform transcendental implementations. Prepared channel hashes are:

- load: `d3128b43dc7fc075b7ac0a192e09563b96848a2ec8549f433bc12624b980e070`;
- wind: `29739d37bcc0d459157737fa7128e2ab6e74081c7b3a3d8fdfa9dc425146d9f9`;
  and
- solar: `710a5be5ada057cdbe39f0b86d1d40bef54d428960581b53e10257e401ef775e`.

The connected electrical-distance matrix uses
$$\max(|x|,10^{-6})$$ pu and hashes to
`370643318261ace9747f771eedc95bf4b1e106d2a77892e90e0a3fa6f6e1293c`.
Deterministic load-weighted medoids are buses 41, 65, 89, and 105. The solar
and wind sites are buses 65 and 105.

The predeclared eight-point pilot grid crosses 15%/30% annual available
renewable energy, 5%/10% peak-load storage power, and 4/8-hour storage
duration. `S0_PILOT_PROTOCOL.md` defines the selection rule and prohibits
inventing a new point in response to results.

## Matched thermal counterfactual

The effectively unlimited case is a deep copy of the converted PGLib case in
which only branch column 5 (`rateA`) is set to zero. Tests verify equality of
every other value and verify that the rated input is not mutated.

For lossy DC, cvxopf replaces each zero rating with the frozen finite sentinel;
for AC, zero ratings omit the thermal constraint. This is not described as a
mathematically unconstrained network.

## Preliminary static probes

These probes use the source operating point, no storage, no renewables, and no
load shedding. They characterize construction and local-solver behavior; they
are not yet the complete independently audited S0 gate.

| Network | Formulation | Status | Objective | Solve time | Maximum flow metric |
|---|---|---:|---:|---:|---:|
| PGLib rated | lossy DC | `optimal` | 93,028.275 | 0.010 s | 664.66 MW |
| PGLib rated | AC | `optimal` | 97,213.607 | 6.07 s | 512.33 MVA |
| PGLib effectively unlimited | lossy DC | `optimal` | 93,028.274 | 0.008 s | 664.72 MW |
| PGLib effectively unlimited | AC | `optimal_inaccurate` | 96,881.511 | 7.18 s | 511.64 MVA |

The rated AC solution reaches 1.000 terminal utilization and its voltage range
is 0.98439–1.06000 pu. The effectively unlimited AC result has a
0.99869–1.06000 pu voltage range. Its `optimal_inaccurate` status is retained
as diagnostic evidence only until it passes the declared independent residual
gate.

A six-hour constant-input probe produced:

| Formulation | Status | Objective | Construction | Solve | Scalar variables | CVXPY constraints |
|---|---:|---:|---:|---:|---:|---:|
| lossy DC | `optimal` | 558,169.647 | 0.011 s | 0.042 s | 1,440 | 24 |
| AC | `optimal` | 583,281.644 | 1.361 s | 294.615 s | 13,656 | 8,022 |

The approximately 4.9-minute six-hour AC solve is the first measured warning
against assuming linear throughput. It does not alter the predeclared scaling
ladder.

The lowest pilot point (15% renewable energy, 5% peak storage power, four-hour
duration) has also passed the complete independent one-hour M17 audit on the
rated network after profile quantization:

- lossy DC: `optimal`, objective 98,052.902;
- AC: `optimal`, objective 110,339.619;
- maximum lossy-DC reconstructed residual below $$1.8\times10^{-15}$$ except
  floating-point SoC/reporting terms below $$1.2\times10^{-13}$$; and
- maximum AC reconstructed residual below $$2.4\times10^{-13}$$, with zero
  voltage, thermal, negative-curtailment, and negative-branch-loss residuals.

Both results satisfy the fixed M17 accepted-primal tolerances. The one-hour
equality endpoint forces zero net storage energy movement; the six-hour pilot
is still required to establish nontrivial cycling.

The earlier unquantized exploratory AC probe reached a different accepted
local objective. It is superseded rather than mixed with the frozen pilot
record. This is another concrete reason that authoritative nonlinear runs must
use committed prepared inputs and retain exact solver provenance.

## Frozen six-hour pilot result

The four-case run executed from clean commit `634dc1f` and all public results
passed the complete independent audit. The ignored artifact is integrity-bound
by `S0_RESULTS_METADATA.json`.

| Network | Formulation | Status | Solve | Objective | Maximum utilization | Storage throughput |
|---|---|---:|---:|---:|---:|---:|
| rated | lossy DC | `optimal` | 0.068 s | 551,459.482 | 1.000000 | $$3.43\times10^{-6}$$ MWh |
| rated | AC | `optimal` | 585.25 s | 616,867.742 | 1.000000 | $$1.95\times10^{-5}$$ MWh |
| effectively unlimited | lossy DC | `optimal` | 0.057 s | 550,512.665 | 0.000985 | $$6.81\times10^{-7}$$ MWh |
| effectively unlimited | AC | `optimal_inaccurate` | 117.14 s | 587,031.448 | — | 6.001 MWh |

The effectively unlimited lossy-DC maximum flow is less than 0.1% of the
frozen 1,000,000 MW sentinel, comfortably satisfying the tenfold-margin rule.
The unlimited AC result's accepted status follows from the complete residual
gate, not from treating `optimal_inaccurate` as sufficient by itself.

The process peak RSS rose from 171 MiB before rated AC to 14,541 MiB after its
solve. This is a cumulative `ru_maxrss` observation: it establishes a serious
peak-memory cost but cannot show release or isolate the subsequent unlimited
case.

### Pilot-selection hold

The rated solutions place branches at their enforced limits but move storage
only at numerical-noise scale. The unlimited AC solution moves 6.001 MWh and
curtails 35.585 MWh, but it is not the primary rated scenario.

The protocol says the selected point must show “nonzero storage movement” but
does not define a numerical threshold or state explicitly that the movement
must occur in the rated case. Exploiting the literal nonzero floating-point
values would be scientifically misleading. This first point is therefore not
selected yet. Before another grid point is run, review must freeze a
tolerance-aware movement criterion and its rated/control scope.

### Reviewed amendment

Review approved both missing decisions before any further OPF execution:

- meaningful movement must pass both the per-device instantaneous and
  aggregate-throughput gates defined in `S0_PILOT_PROTOCOL.md`, specifically
  in the primary rated AC result; and
- one common six-hour window is selected from the 15% renewable reference net
  load by the frozen low-earlier/high-later score.

The selected replacement interval is boundaries 3757–3763, split at five
hours, corresponding to `2025-06-06 13:00` through `18:00` UTC. The midnight
artifact remains an integrity-bound characterization but is superseded for
pilot selection. The lowest pilot point will be rerun from scratch after this
amendment is committed.

## Amended-window execution and selection

The lowest pilot point was rerun from scratch at clean commit `578b270`. All
four cases passed the complete audit, and the rated AC result passed both
precommitted movement conditions:

- maximum device power was 205.946 MW, compared with a largest applicable
  per-device threshold of 0.206 MW; and
- total throughput was 465.760 MWh, compared with a 1.084 MWh threshold.

The lowest grid point is therefore selected: 15% annual available renewable
energy, aggregate storage power equal to 5% of peak load, and four-hour
storage duration.

| Network | Formulation | Status | Solve | Objective | Maximum utilization | Storage throughput |
|---|---|---:|---:|---:|---:|---:|
| rated | lossy DC | `optimal` | 0.061 s | 402,195.889 | 1.000000 | $$1.24\times10^{-6}$$ MWh |
| rated | AC | `optimal` | 2,068.60 s | 441,231.518 | 1.000000 | 465.760 MWh |
| effectively unlimited | lossy DC | `optimal` | 0.061 s | 402,102.445 | 0.000773 | $$6.17\times10^{-7}$$ MWh |
| effectively unlimited | AC | `optimal` | 1,326.68 s | 425,541.122 | — | 478.287 MWh |

The rated/nonrated contrast is not merely an outer-plan effect: both lossy-DC
solutions leave storage essentially idle, while both nonlinear AC solutions
cycle materially under the same equality endpoint. The rated AC solution also
uses an enforced branch limit. This makes the selected case a nontrivial test
of the DC-to-AC hierarchy rather than a large but inactive storage example.

Peak process RSS reached 14,662 MiB. Combined with 34.5- and 22.1-minute AC
solve calls, this rules out treating a direct 24-hour AC solve as an ordinary
S1 expectation. S1 requires an explicit wall-time limit and memory policy.

## Repository case118 comparator

The repository `case118()` is retained only as an implementation and scale
comparator. It has the same 118 buses, 54 generators, 186 active branches,
100 MVA base, and aggregate 4,242 MW / 1,438 MVAr demand as the converted
PGLib case. It is not a matched congestion counterfactual:

- aggregate active-generator maximum is 9,966.2 MW rather than 6,515 MW;
- all 186 `rateA` values are 9,900 MVA rather than the PGLib 72–7,218 MVA
  range; and
- 235 bus, 177 generator, 932 branch, and 108 cost-table entries differ.

Its exact array hashes are retained in `S0_COMPLETION_METADATA.json`. No
scientific rating-sensitivity conclusion compares this case with PGLib.

## S1 resource authorization

S0 freezes the following limits for this workstation:

- 16 GiB process RSS;
- 45 minutes for any individual AC solve;
- two hours for the complete S1 run; and
- RSS observation at least once per second in a supervised child process.

Direct AC is authorized only through six hours. The 24-hour direct-AC
comparator is recorded as `not_authorized_by_s0_resource_gate`; it is not run
and is not classified as solver failure or infeasibility. S1 may proceed with
24-hour lossy DC and bounded AC/hierarchical construction measurements. A
resource limit terminates the child process and produces an explicit retained
resource-boundary record.

This is already a substantive scaling result: decomposition is needed not
only to address feasibility. On this frozen case, a monolithic nonlinear model
reaches a practical memory and runtime boundary at a six-hour horizon.
