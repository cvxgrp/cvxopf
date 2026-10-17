# Selected segment escalation implementation

The report-only segment selector is implemented in `segment.py`. It checks
only explicitly requested global hours and complete five-/six-bus loops
touching a specified bus/branch neighborhood. It previews by default;
`--run` opts into the unchanged bounded proof, with 64 boxes per selected
loop-hour. Default three-/four-bus checking and the existing batch drivers
remain unchanged.

The full physical network, parallel branches, transformer data, input frames,
battery powers and source horizon remain intact. A selected global hour uses
its original offset in those full inputs; neighborhood selection does not
create a reduced OPF or change initial SoC, dynamics or endpoints. Invalid
selectors fail rather than widening the scope. Empty selections remain
unresolved. See `README.md` and `SEGMENT_PROTOCOL.md` for exact semantics.

The retained-data smoke test was preview-only, not a numerical escalation.
It independently replayed original transfer job 13 and selected hours 2452
and 2453 with bus 71 and one corridor hop. The neighborhood was
`[70, 71, 72, 73]`, selecting two complete cycles:

- `[69, 70, 74, 75, 77]`
- `[69, 70, 75, 118, 76, 77]`

It omitted the other 55 longer loops and 22 hours. The four selected loop-hour
pairs have a dispatch cap of 256 boxes plus at most two cached geometry-root
checks if explicitly executed. The preview executed **zero proof boxes and
zero OPF solves**. Original offsets 13 and 14 were retained. The original
SOCP transfer's timeout label remained a timeout, not an accepted result.
Full preview provenance is retained in `SEGMENT_PREVIEW.json`.

This example demonstrates selection only: the existing shorter-loop
certificates already explain these historical problem hours, so the example
is not a reason to repeat their numerical investigation.

29 new no-solve regressions passed. They cover selected-only hour/loop work,
original offsets, full-network preservation, bus/branch unions, hop expansion,
inactive and reversed/parallel transformer rows, duplicates/iterators, invalid
requests, preview/empty behavior, exact equality with the frozen proof on a
synthetic circuit, complete exclusions and unsupported outcomes, one-job
historical replay, separate AC controls, and fatal witness/hash inconsistencies.

The full relevant preflight, historical replay, loop, slice and feasibility
suites passed **164 tests** in 48.89 seconds. Ruff lint and format checks passed
for the two new Python files. One existing CVXPY dependency-import warning
about mixed OpenMP runtimes was reported; no optimizer was invoked.

Only the current-policy README was updated among pre-existing files. All
previous proof sources, protocols, reports, selected evidence and raw study
artifacts remain byte-for-byte unchanged. No retained-data subdivision or
full-cohort back-test was rerun in this task. No new OPF solves, repair, retry,
physical changes, staging or commit occurred. Source E3/E3+ qualifications and
historical Stage D remain untouched.
