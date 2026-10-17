# Forced five-/six-bus subdivision back-test

Bypassing the positive-export trigger did not produce additional exclusions
under the unchanged 64-box cap. All **32,832 loop-hour checks** exhausted that
cap and remained unresolved. No tested box was excluded at all; each search
ended with 65 unresolved frontier boxes. This is not a feasibility certificate.

## Coverage and outcomes

One combined pass checked every retained hour for 18 original transfers and
six accepted AC controls: 432 donor hours plus 144 control hours. All 27
five-bus and 30 six-bus loops entered dispatch-dependent subdivision regardless
of whether their necessary export bounds were positive. There were no skipped
long-loop checks, unsupported checks, new certificates or accepted-witness
contradictions.

| Cycle size | Unique loops | Donor loop-hours | Control loop-hours | New certificates | Cap-exhausted searches |
|---|---:|---:|---:|---:|---:|
| Five buses | 27 | 11,664 | 3,888 | 0 | 15,552 |
| Six buses | 30 | 12,960 | 4,320 | 0 | 17,280 |

In particular, SOCP, lossy DC and copper-plate donated powers at hour 2452
each yielded 57 unresolved longer-loop searches, as did the accepted AC
control. The previous four-bus certificates are preserved separately: SOCP
at hours 2452 and 2453, lossy DC at 2452, and copper plate at 2452. This new
pass neither replaces nor weakens those complete exclusions.

Independent historical replay retained the original outcomes exactly: 15
compatible transfers, two native rejections and one timeout. No rejected or
censored result was relabelled. The accepted outcomes still come from their
retained independent audits, not from this inconclusive interval scan.

## Interpretation

The trigger was bypassed, not replaced with invented positive exports. All
original necessary cut bounds, including negative ones, were supplied to the
unchanged multigraph proof. None of the tested boxes had an empty necessary
branch-angle intersection or a cycle sum excluding every integer winding.
The search performed 64 bisections per loop-hour, visited the root plus 63
other boxes, and left 65 frontier boxes. No whole-domain certificate followed.

Thus the earlier trigger was not hiding a certificate discoverable by this
specific 64-box search on these retained data. This does not establish that a
larger budget or stronger bounds would also fail. It does not establish
physical feasibility: the loop tests are conservative necessary conditions,
not a complete encoding of all AC constraints. None of these longer loops
contains buses 71 or 72, as in the preserved catalogue.

## Method and preservation

`forced_subdivision.py` is a separate extension beside the frozen multigraph
checker. It inherits the existing branch equations, directed Decimal interval
arithmetic, transformer coordinates, separate parallel-branch restrictions,
winding tests and voltage-box splitting order. Only the screening trigger is
bypassed for cycle lengths five and six. Shorter-loop and stand-alone parallel
corridor outcomes remain in the prior checkpoints; they were not rerun here.

Every search retained a cap of 64 visited boxes, including its dispatch root.
There were 2,101,248 dispatch-dependent box visits plus 1,368 cached geometry
root checks (57 loops for each of 24 schedules): 2,102,616 actual box visits.
Exact branch arithmetic was memoized. The evidence also tracks exact whole-
search reuse separately; none occurred in this run. No approximate cache keys
or changed numerical policy were used.

All pinned battery powers, physical OPF constraints, SoC policies, source
inputs and audit gates remain unchanged. The independent replay and all
analysis processes ran inside the no-optimization guard. **No new OPF solves**
were run, no historical solver effort was reset, and source E3/E3+
qualifications and historical Stage D remain untouched.

All 192 files present in the four analysis experiment directories before
launch, excluding bytecode caches, were verified byte-for-byte unchanged at
completion. Historical transfer artifacts and input bindings were additionally
verified by the existing replay machinery. Earlier five-/six-bus evidence and
the old proof implementations were not edited. New files are left unstaged.

The completed four-process pass ran from 2026-10-12T23:00:36.048335+00:00 to
2026-10-12T23:24:45.522905+00:00 (1449.480 elapsed seconds).
This is report-only analysis time, not new supervised OPF worker effort.
An initial serial pilot was interrupted after 13 completed hours solely to
switch execution to separate processes. Its raw log is retained in
`results/forced-subdivision-pilot.log`; it is not counted as a completed
back-test or certificate. The completed pass log is retained separately.

## Evidence and reproduction

`FORCED_SUBDIVISION_EVIDENCE.json` shares one ordered topology and losslessly
encodes every loop-hour disposition. Identical inconclusive counters are
grouped by explicit cycle indexes; every 64-visit/65-frontier result can be
reconstructed. Certificate evidence, had any been found, would retain all
terminal boxes and current cut provenance rather than reuse a stale proof.

Regressions check exact equality with the frozen uncached proof on synthetic
five-/six-bus circuits, feasible parallel-transformer witnesses, unsupported
geometry handling, exact repeat reuse, and lossless counter grouping. The
selected evidence regression checks complete coverage, cap accounting, source
fingerprints and preserved study files. Final verification results are recorded
in `FORCED_SUBDIVISION_VALIDATION.json`.

```bash
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --extra dev python \
  -m experiments.tracy_socp_transfer_preflight.forced_subdivision
```

The command prints a fresh report without overwriting saved study artifacts.
This remains retrospective scientific consistency testing, not holdout
prediction, complete infeasibility detection or global optimality.
