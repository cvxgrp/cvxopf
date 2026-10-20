# Hour 2452: local loop infeasibility and SOCP non-tightness

## Outcome and scope

All three retained donor battery-power vectors at hour 2452 are incompatible
with the unchanged modeled AC constraints. This conclusion is supported by a
computer-assisted necessary-condition infeasibility certificate on the
71–72–24–70 loop and its radial 71–73 branch, not by IPOPT's termination status.
The accepted AC control is not excluded by the same necessary conditions.

No OPF optimizer was invoked. The calculation uses retained inputs, exact
AC branch equations including charging, original voltage bounds and
both-terminal apparent-power limits. All battery powers, SoC policies,
production preparation and numerical policies remain unchanged. The proof
uses only a subset of the full model's constraints; excluding that larger
local feasible set excludes the full network at these pins. It does not
establish incompatibility of other schedules or claim global optimality.

The proof and retained-point measurements are reproducible in `analyze.py`;
`EVIDENCE.json` retains source hashes, directed arithmetic bounds and every
terminal voltage box. Original E3/E3+, transfer, alternative-start, feasibility
diagnostic and four-case single-hour archives have not been edited or relabelled.
Production commit remains `b11c4418fbd8f81294a1a26a1c053bb896f3b216`.

## Necessary power routing

At hour 2452, maximum served real loads at buses 72 and 73 are respectively
12.747218075509204 and 6.373609037754602 MW. Real generation at both buses is
fixed to zero; bus 73 has no battery and connects only to 71. Wind/solar real
injections are nonnegative and therefore cannot absorb the donated surplus.

Real loss on 71–73 is at most 1.013647241200275 MW, conservatively allowing
the full bus-73 reactive range (100 MVAr generator plus 0.910018515836123 MVA
solar inverter), all load service levels and charging. Real loss on 71–72 is
at most 13.118514608486094 MW from its 159-MVA limit and voltage bounds.
These are upper bounds, not the retained point's losses.

Writing positive flows as leaving their first bus, real balances imply:

```
P71→72 ≥ B71 − max(load73) − max(loss71–73) − 166 MW
P72→24 ≥ P71→72 − max(loss71–72) + B72 − max(load72).
```

The loss bound follows from `Iseries ≤ |Sterminal|/(baseMVA*Vmin) + (bcharge/2)*Vmax`
and `loss = baseMVA*r*|Iseries|²`. At bus 73, the known maximum real and
reactive withdrawals give a tighter terminal-current bound than its 188-MVA
line rating. Shedding loads or supplying additional real renewable power
does not relax these lower export bounds.

| Pins | B71, MW | B72, MW | Required P71→72, MW ≥ | Required P72→24, MW ≥ |
| --- | ---: | ---: | ---: | ---: |
| SOCP | 296.844267005 | 32.934484978 | 123.457010726 | 130.525763019 |
| Lossy DC | 296.844266996 | 29.703450309 | 123.457010717 | 127.294728342 |
| Copper plate | 287.407028430 | 26.383987669 | 114.019772151 | 114.538027136 |

These power bounds alone are not the certificate: they are below the respective
159- and 146-MVA ratings. The obstruction appears when their required voltage
angle differences must also close the loop.

## Exact angle closure contradiction

Let `δij = θi − θj`. Any AC voltage vector must satisfy

```
δ71,72 + δ72,24 = δ71,70 + δ70,24   (modulo 2π).
```

The original branch circles and voltage bounds place all four principal
angle differences in sufficiently small arcs that their summed range is
less than π; no alternate 2π winding can evade the equality.

For a branch with series admittance `y=g−jb`, magnitudes `u,v` and angle
difference `δ`, the unchanged real-power equation is

```
Pij/baseMVA = g*u² + u*v*(-g*cos(δ) + b*sin(δ)).
```

On the thermally allowed arc this is strictly increasing in δ. Each required
real transfer therefore supplies the necessary lower angle bound

```
δ ≥ atan(g/b) + asin((Pmin/baseMVA − g*u²)/(|y|*u*v)).
```

For the return-path branches, retain both terminal circles exactly. Set
`c=bcharge/2`, `A=g+j*(b−c)`, `C=A*y`, and `α=arg(C)`. The sending circle is

```
cos(α−δ) ≥ k(u,v)
k = (|A|²*u⁴ + |y|²*u²*v² − (rating/baseMVA)²)/(2*|C|*u³*v).
```

The receiving circle gives the same bound with `u,v` exchanged and δ negated.
Thus the upper allowed angle is the smaller of the two terminal bounds.
To avoid interval cancellation, the same k is evaluated as

```
k = 1 + (u−v)²/(2*u*v)
      + (|A|²−|C|)*u/(2*|C|*v)
      + (|y|²−|C|)*v/(2*|C|*u)
      − (rating/baseMVA)²/(2*|C|*u³*v).
```

This is an algebraic identity, not a relaxed physical equation or changed gate.
An initial cancellation-prone interval expression exhausted its bounded
coverage check without a conclusion; this equivalent expression supplied
tighter enclosures. No optimizer was run in either calculation.

The voltage domain is the complete `[0.94,1.06]⁴`, not selected voltage points.
For SOCP, the entire domain gives

```
δ71,72 ≥ 11.23805598°       δ72,24 ≥ 13.47380084°
δ71,70 ≤  3.95188380°       δ70,24 ≤ 19.89990779°
required path ≥ 24.71185682° > allowed return path ≤ 23.85179159°.
```

Lossy DC is excluded by the same whole-domain calculation. Copper plate
requires ten disjoint voltage boxes, whose union exactly covers the original
domain; every box has a strictly positive closure contradiction.

| Pins | Excluded terminal boxes | Unresolved boxes | Smallest certified angle margin |
| --- | ---: | ---: | ---: |
| SOCP | 1 | 0 | 0.8600652283° |
| Lossy DC | 1 | 0 | 0.4368510720° |
| Copper plate | 10 | 0 | 0.0309797360° |

Margins are conservative lower bounds over the reported partition, not
estimates of the actual minimum physical discrepancy. Arithmetic uses
36-digit directed Decimal intervals, outward-enclosed square roots and
70-term atan Taylor bounds with an explicit remainder. Input binary64 values
are converted exactly to Decimal. The proof uses no sampled minimum, numerical
optimizer status, small-angle approximation or inferred solver feasibility.

## How relaxed was the SOCP donor?

For each retained edge-hour, measure

```
Δij = wi*wj − (Wij,re² + Wij,im²) ≥ 0
relative slack = Δij/(wi*wj).
```

Δ has units pu⁴; relative slack is dimensionless. An exact AC voltage product
has zero slack. Positive slack is permitted by SOCP, not an inequality
violation. Signed values, including tiny negative rounding residuals, are
retained without clipping. The `1e−5` relative threshold below is the existing
rank-tightness diagnostic, not a new acceptance gate.

At hour 2452 the worst relative slack is **18.15154465%**, on **59–63**:
Δ = **0.217924942733 pu⁴**. Six of 179 edge products exceed `1e−5`; mean
relative slack is 0.24571345%, and the 95th percentile is only 8.258e−10.
The slack is concentrated rather than uniformly large at this hour.

Across this donor's complete retained 24 hours, the maximum is **24.16950582%**
(54–59, hour 2462), mean 2.55174396%, and 3406 of 4296 edge-hours exceed `1e−5`.

### Matched Tracy historical comparison

All rows use the same relative-slack definition and 24×179 edge-hours. Five
retained accepted relaxation points are compared; the separate forced-shedding
point keeps its original rejection and is not counted as accepted evidence.

| Retained SOCP scenario | Maximum slack over 24 hours | Mean slack |
| --- | ---: | ---: |
| Large surplus, neutral | 25.12832774% | 3.23954658% |
| Large deficit, neutral | 22.70933196% | 0.74018217% |
| Large deficit, depletion | 22.84871795% | 0.86066486% |
| Surplus to deficit, neutral | 23.58763410% | 1.33800005% |
| **Deficit to surplus, neutral (this donor)** | **24.16950582%** | **2.55174396%** |
| Forced shedding, saved rejection — descriptive only | 19.48112649% | 0.28799861% |

The target is second-largest among the five accepted Tracy points by both
maximum and mean slack, but is not an outlier relative to that group.
The forced-shedding record's saved classification is `rejected` and its saved
relaxation audit is false. Its original E3+ owner-approved qualifications are
not changed by including descriptive measurements here.

Compared with earlier, smaller M11 matched benchmarks, these Tracy slacks
are much larger: Case9 maxima are 0.89050928% (E2 v1) and 0.80829810% (E2 v2);
Case14 and the mixed cases have only order 1e−9 or smaller positive normalized
slack. These are comparisons of different problems and retained points, not
an attribution to solver accuracy or a relaxation objective-gap certificate.

### The decisive local relaxation is angle consistency

At hour 2452 every edge of the questioned loop is essentially cone-tight:

| Directed edge | Relative cone slack | Implied angle difference |
| --- | ---: | ---: |
| 71→72 | 1.5751e−11 | +12.34478881° |
| 72→24 | 1.5109e−11 | +14.82371426° |
| 24→70 | 7.8721e−10 | +15.44099755° |
| 70→71 | 2.1503e−12 | −3.05536134° |

Their directed sum is **39.55413928°**, not zero modulo 360°. The radial
71→73 edge is also tight (relative slack 5.2821e−12) and serves its true
6.373609 MW load in the original SOCP point; it is not the fictitious sink
seen in the rejected AC primals. Here SOCP supplies locally valid branch
transfers but no globally consistent AC voltage angles. This illustrates the
separate edge-tightness and cycle-consistency conditions described by
[Farivar and Low](https://smart.caltech.edu/papers/relaxconvex2parts.pdf).

This explains how the SOCP donor could pass its relaxation audit while its
battery powers are impossible in AC. It also gives a physical obstruction
common to the two other donor schedules. It does not explain every detail of
IPOPT's path or why its retained residual was concentrated specifically at 73.

## Verification and reproduction

Fresh `run.verify` checks the unchanged four-case source/environment binding,
protocol, predecessor bytes and historical artifacts. The retained E3 viewer
checks completion/supervision and archive hashes. Historical M11 point paths
and hashes are retained separately in `EVIDENCE.json`.

Tests check directed arithmetic against exact rational endpoint operations,
π/atan against independent decimal constants, the terminal-circle identity
against direct complex currents, and exact non-overlapping coverage of every
certified voltage box. The original accepted control is not excluded, both
over the full domain and at its retained voltage point. Optimizer calls are
blocked during analysis and tests. The eight analysis tests and 23 existing
single-hour tests passed together (31 total); lint and format checks passed.

From the repository root:

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
VECLIB_MAXIMUM_THREADS=1 UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache \
uv run --extra dev python -m experiments.tracy_battery_transfer_loop.analyze

UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --extra dev python -m pytest \
  experiments/tracy_battery_transfer_loop/test_analysis.py -q
```

The thread environment matches the frozen binding for verification; this
command only analyzes retained inputs and emits evidence to stdout.
All new work is unstaged in this separate experiment directory. No completed
solve was repeated and no new initialization search is warranted for these
three pins unless the research question or modeled assumptions change.
