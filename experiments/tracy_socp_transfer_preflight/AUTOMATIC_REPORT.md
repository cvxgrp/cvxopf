# Automatic loop certificate back-test

The certificate check now discovers topology rather than selecting particular
bus numbers. Its retrospective back-test excludes the same three donated
24-hour schedules, with no contradiction among 15 independently audited
compatible transfers or six accepted AC controls. It also discovers a second
excluded SOCP hour, 2453. No new OPF solves were run.

## Method and coverage

`automatic_loops.LoopScanner` enumerates simple cycles and constructs local
cut balances from contiguous cycle arcs, including automatically detected
pendant components. It derives necessary directed exports from unchanged
battery pins, device ranges, signed shunts, internal loss bounds, and other
boundary ratings. Both-terminal thermal angle bounds and forward/reverse
export bounds must admit angle closure modulo 2π. Directed Decimal intervals
and complete voltage-box partitions supply the exclusion, not sampled points
or the retained SOCP angle residuals.

The default search discovers all 51 simple three-/four-bus cycles in the
retained topology. Forty-three support the current geometry assumptions;
seven contain parallel edges and one contains an unsupported transformer tap
or phase shift. Those eight are explicitly unresolved. Longer loops are not
covered by the default; the maximum length is configurable through six.

Across 432 donor hours (22,032 loop-hour checks):

| Loop-hour disposition | Count |
|---|---:|
| Complete exclusion certificate | 4 |
| Unresolved at the 64-box budget | 6 |
| Unresolved, no positive necessary-export trigger | 18,566 |
| Unsupported geometry | 3,456 |

No-trigger, unsupported, or budget-exhausted checks are not feasibility claims.
The six controls add 144 scanned hours with no exclusion. Witness acceptance
comes from the existing independent audit, not from this scanner.

## Certificates

All four certificates automatically identify the loop 24–70–71–72–24,
equivalent to the previously studied 71–72–24–70 loop.

| Donor | Historical 24-hour transfer outcome | Excluded hour | Voltage boxes visited | Minimum certified closure gap, degrees |
|---|---|---:|---:|---:|
| SOCP | Timeout, preserved | 2452 | 1 | 15.930479 |
| SOCP | Timeout, preserved | 2453 | 9 | 0.082207 |
| Lossy DC | Native rejection, preserved | 2452 | 1 | 13.615994 |
| Copper plate | Native rejection, preserved | 2452 | 1 | 6.549691 |

The 2453 proof partitions the full four-dimensional voltage box into five
excluded leaves. The other proofs exclude the root box. Exact Decimal bounds,
cut bus sets, export terms, per-edge angle intervals, voltage boxes, and closure
gaps are retained in `AUTOMATIC_EVIDENCE.json`; degree values above are display
conversions only.

The generalization strengthens the old directional test by using every
supported arc cut in both directions. It finds `{71,73}` and `{71,72,73}`
automatically and also bounds the formerly weak return legs. That is why it
finds the additional SOCP hour and larger 2452 gaps; no physical limit or
acceptance tolerance was changed.

## Evidence boundaries and verification

All 18 original transfer manifest/resource/compatibility outcomes were replayed
using the existing read-only machinery: 15 compatible, two native rejections,
one timeout. The scanner's independent exclusions are additional evidence,
not replacements for those outcomes. The original named checker, its reports,
the endpoint-fix evidence, and historical experiment bundles were hash-checked
unchanged before/after the back-test. Source E3/E3+ qualifications remain intact.

Regressions cover the three known donor exclusions, nonconsecutive bus
relabeling, bus/branch row permutations and branch reversal, an unrelated
synthetic triangle, a feasible rank-one witness with a positive export trigger,
signed shunts/multiple signed devices, unsupported asset handling, integer
windings at varied ambient precision, and exact partition coverage for the new
hour. Existing named-checker, preflight, and slice tests remain in scope.

The scientific-review standard is retrospective consistency on retained data,
with explicit unsupported coverage. This is not holdout predictive validation,
complete automatic infeasibility detection, or a global optimality certificate.
All implementation and output additions are confined to this experiment;
production AC/device code and physical constraints are unchanged.

See `AUTOMATIC_PROTOCOL.md` for the derivation and replay command.
