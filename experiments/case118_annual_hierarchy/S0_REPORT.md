# S0 report — source and static operating-point characterization

**Status:** In progress

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

## Remaining S0 work

- Verify storage recurrence, branch limits, device signs, and independent
  residuals on rated and matched-control six-hour pilot cases.
- Characterize repository case118 separately as a scale comparator.
- Review the pilot evidence, then freeze authoritative sizing and machine
  resource budgets before S1.
