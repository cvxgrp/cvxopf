# Forced five-/six-bus subdivision checkpoint

This report-only extension bypasses the strict-positive-export screening trigger
for every supported five- or six-bus loop at all 432 retained donor hours and
144 accepted AC-control hours. Run one combined catalogue (27 five-bus and 30
six-bus cycles) without duplicating five-bus work. Retain the original 64-box
visit cap **per loop-hour**, including its dispatch-dependent root box.
Cached geometry-only root checks are unchanged and separate from that cap.
Unsupported geometries remain unsupported; failure to exclude the entire root
voltage box remains unresolved, never an acceptance or feasibility certificate.

The multigraph proof, Decimal directed rounding, passive-branch assumptions,
parallel-branch intersections, fixed taps/shifts, cut-bound mathematics and
voltage-box splitting order are inherited unchanged. Necessary bounds may be
negative; they are not replaced with invented positive bounds. Exact-key
memoization reuses branch arithmetic and inconclusive searches only when all
mathematical inputs to that search are identical within one scanner. Record
actual and logical box visits separately; every reused search has the same cap.
Certificates always retain their own current cut provenance and full leaves.
Four separate Python processes handle independently verified transfers;
each retains its own per-loop cap and no-optimization guard. Exact cut bounds
are computed once per loop-hour and reused for its trigger-bypassed search.

All pinned hourly battery powers, physical OPF constraints, SoC policies,
source inputs, audit gates, numerical solves and historical classifications
stay unchanged. Independent retained replay runs inside the no-optimization
guard. Preserve all existing study files byte-for-byte, including the separate
triggered five-/six-bus evidence. Three-/four-bus and parallel-corridor outcomes
stay in those baseline checkpoints, not a new unrequested search here.

Selected evidence shares topology, losslessly run-length encodes each hour's
loop statuses, and groups identical inconclusive counters by explicit cycle
indexes. Every loop-hour disposition and its visited/unresolved/excluded count
can be reconstructed. Run without solving or overwriting existing evidence:

```bash
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --extra dev python \
  -m experiments.tracy_socp_transfer_preflight.forced_subdivision
```

This is retrospective scientific consistency testing, not holdout prediction,
complete infeasibility detection, a relaxation tightening or global optimality.
Source E3/E3+ qualifications and historical Stage D remain untouched.
