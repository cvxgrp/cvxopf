# Parallel-branch and transformer loop checks

All eight previously unsupported three-/four-bus loops are now covered by
the report-only checker. The extension also checks seven parallel corridors,
including their two-branch cycles. No new OPF solves or physical-model changes
were made. Prior source, reports and selected evidence remain unchanged.

## What changed

Parallel branches retain separate row identities, terminal ratings, charging,
tap/phase data and losses. Cut balances first bound total corridor export;
each member's necessary export subtracts the other members' ratings. All
members must permit the same bus-angle difference modulo 2pi. There is no
equivalent-line substitution or guessed allocation of real power.

For a fixed from-side transformer `t = tau exp(j phi)`, the branch calculation
uses internal voltage `V_from/t`, unchanged physical terminal ratings, and
electrical angle `theta_from - theta_to - phi`. The resulting angle interval
is shifted back by phi. Traversal reversal changes the shift sign, and
circular intersections retain possible integer windings. Series-current loss
bounds use internal voltage; bus injection/shunt bounds remain physical.

Independent complex-admittance fixtures check this coordinate identity,
terminal powers, losses, nonunit taps, signed phase shifts, and reversal.
Synthetic tests retain feasible unequal/reversed parallel-transformer points,
including phase wraparound, and detect incompatible phase shifts or tap/voltage
mismatches without solving.

## Retained-data back-test

The default now checks 51 simple three-/four-bus cycles plus seven shared-angle
parallel-corridor groups: 58 checks per hour. All eight old coverage gaps are
closed in every retained schedule. In this topology each parallel corridor has
two members. In general a corridor group includes every member rather than
enumerating each edge-resolved combination separately.

| Formerly unsupported loop | Newly handled feature |
|---|---|
| 54–55–56–59–54 | Parallel 56–59 branches |
| 54–55–59–56–54 | Parallel 56–59 branches |
| 54–56–59–54 | Parallel 56–59 branches |
| 55–56–59–55 | Parallel 56–59 branches |
| 59–61–64–63–59 | Fixed taps on 64–61 and 63–59 |
| 77–78–79–80–77 | Parallel 77–80 branches |
| 77–80–96–82–77 | Parallel 77–80 branches |
| 89–90–91–92–89 | Parallel 89–90 and 89–92 branches |

All 18 original transfer outcomes were independently replayed using existing
manifest/resource/compatibility machinery: 15 compatible, two native rejections,
one timeout. All 432 donor hours and 144 accepted-AC-control hours were checked.
No certificate contradicts any of those 15 successful transfers or six controls.

Across 25,056 donor loop-hour checks:

| Disposition | Count |
|---|---:|
| Complete exclusion certificate | 4 |
| Unresolved at the 64-box dispatch-dependent budget | 6 |
| Unresolved, no positive necessary-export trigger | 25,046 |
| Unsupported geometry | 0 |

The conclusions are unchanged: SOCP is excluded at 2452 and 2453; lossy DC
and copper plate are each excluded at 2452. All four certificates still
identify 24–70–71–72–24. None of the eight newly handled loops produces an
additional exclusion in this back-test. Original timeout/rejection labels,
source E3/E3+ qualifications and historical artifacts remain untouched.

Every supported loop receives a cached geometry-only root check, even without
an export trigger; nonzero phase shifts can themselves prevent closure. A
positive necessary aggregate export triggers the subsequent bounded voltage
partition. Detailed branch/cut/angle evidence and historical/checkpoint hashes
are in `MULTIGRAPH_EVIDENCE.json`.

## Scope and use

The scientific-review boundary remains a conservative necessary-condition
test, not a feasibility oracle. Unresolved means no certificate was found,
not that a donated schedule is feasible. Positive fixed taps and arbitrary
finite signed shifts are supported subject to passive, positive-rated,
modest-charging, small-electrical-angle assumptions. Missing ratings, HVDC,
invalid taps and unsupported large-angle geometry remain explicit limitations.
Default discovery does not cover longer simple cycles; the maximum length is
configurable through six. This is retrospective consistency testing, not
holdout prediction or a global optimality certificate.

Run the new version without optimization:

```bash
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --extra dev python \
  -m experiments.tracy_socp_transfer_preflight.multigraph --backtest
```

Implementation: `multigraph_loops.py`; replay driver: `multigraph.py`;
derivation: `MULTIGRAPH_PROTOCOL.md`; regressions: `test_multigraph.py`.
The original automatic version is a preserved historical checkpoint.

Validation: 93 tests passed across the multigraph extension, automatic checker,
original preflight, interval analysis and single-hour slice suites. Ruff lint
and formatting checks passed. The existing CVXPY import-time OpenMP-runtime
warning was emitted; no optimizer was invoked.
