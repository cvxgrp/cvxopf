# S1 report — 24-hour outer and bounded AC endpoint

**Status:** Complete

S1 executed from clean commit `ab0cf9d`. The complete ignored artifact is
integrity-bound by `S1_RESULTS_METADATA.json`. Both workers recorded matching
start/end provenance and matched the parent's predeclared context.

## Results

| Network | Record | Outcome | Solve | Peak sampled RSS | Storage throughput |
|---|---|---:|---:|---:|---:|
| rated | 24 h lossy-DC outer | accepted | 0.340 s | — | 151.748 MWh daily; 2.353 MWh matched-window |
| rated | 6 h AC endpoint | accepted | 1,260.23 s | 10,346.5 MiB worker peak | 468.220 MWh |
| effectively unlimited | 24 h lossy-DC outer | accepted | 0.300 s | — | $$3.80\times10^{-6}$$ MWh daily; $$1.02\times10^{-6}$$ MWh matched-window |
| effectively unlimited | 6 h AC endpoint | accepted | 1,156.06 s | 10,987.4 MiB worker peak | 478.285 MWh |

Both direct 24-hour AC records are
`not_authorized_by_s0_resource_gate`. No builder, canonicalization, or solver
was invoked for them. This is a safety classification, not failure or
infeasibility.

All four executed problems returned `optimal` and passed the complete
independent residual audit. The largest DC nodal-balance residual was
$$4.55\times10^{-15}$$ pu. The largest AC active-balance residual was
$$4.55\times10^{-15}$$ pu, and voltage, thermal, negative-loss, and terminal
violations were zero to the reported tolerances.

## Interpretation

The 24-hour convex outer layer is computationally modest: each problem has
6,000 scalar variables and solves in less than 0.35 seconds. The six-hour AC
endpoint problems have 13,752 scalar variables, take 19–21 minutes, and peak
near 10–11 GiB sampled RSS in fresh processes. Construction itself takes less
than 1.6 seconds. Nonlinear solution and canonicalized problem memory—not
Python model construction or the outer optimization—are the observed scale
boundary.

The layer behavior differs materially over the same six-hour interval. The
effectively unlimited lossy-DC optimum selects only
$$1.02\times10^{-6}$$ MWh of storage throughput, while the AC optimum selects
478.285 MWh under the inherited endpoint. The rated lossy-DC optimum selects
2.353 MWh over those six hours, while its AC counterpart selects 468.220 MWh
and reaches an enforced branch limit. The 151.748 MWh rated outer throughput
is retained separately as a full-day summary and is not used as the matched
layer comparison.

These are economic optima, not minimum-correction solutions. They establish
that the AC formulation selected substantial cycling under the inherited
signposts; they do not establish that such cycling was required for AC
feasibility. A fixed-storage feasibility solve or minimum-throughput study
would be needed to answer that separate question.

This is not a sequential trajectory result: no first action was executed, and
the endpoint costs and throughput are not realized-day totals. P0 must still
establish streaming-runner equivalence before week-scale execution.

## S1 conclusion

S1 passes its advancement gate. Both outer plans are accepted, both bounded
endpoint records are accepted, both prohibited direct-AC slots prove
nonexecution, all provenance matches, and neither resource limit was crossed.
P0 is next.
