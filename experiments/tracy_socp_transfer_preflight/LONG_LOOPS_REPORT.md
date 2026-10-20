# Five- and six-bus retained-data back-tests

Both expanded back-tests completed with the unchanged 64-visit budget per
triggered loop-hour. They added 27 five-bus and 30 six-bus loops, but found no
additional exclusions or contradictions with accepted AC witnesses. No new OPF
solves were run. Existing results, bindings, source checkpoints and certificate
evidence were preserved byte-for-byte.

## Coverage and outcome

Each run independently replayed all 18 original transfers and checked all
432 donor hours plus 144 hours of six accepted AC controls. Historical outcomes
remain 15 compatible, two native rejections and one timeout. The independent
preflight still excludes the same three donor schedules, with four loop-hour
certificates: SOCP at 2452 and 2453, lossy DC at 2452, and copper plate at 2452.
All identify the existing 24–70–71–72–24 bottleneck.

| Maximum simple-cycle length | Simple cycles | Parallel corridor checks | Total groups/hour | New certificates vs. length four | Accepted-witness conflicts |
|---|---:|---:|---:|---:|---:|
| Four (preserved checkpoint) | 51 | 7 | 58 | — | 0 |
| Five | 78 | 7 | 85 | 0 | 0 |
| Six | 108 | 7 | 115 | 0 | 0 |

The five- and six-bus additions contain no positive necessary aggregate-export
trigger at any retained donor or control hour. Every supported loop receives
the existing cached geometry-only root check, but these new loops did not
enter dispatch-dependent voltage-box subdivision. Their result is therefore
**unresolved**, not a feasibility certificate or a proof that no bottleneck
exists. This broader topology coverage did not strengthen the conclusions
under the current conservative cut-bound method.

| Newly added cycle size | Unique cycles | Donor loop-hour checks | Control loop-hour checks | New certificates | Budget exhaustion |
|---|---:|---:|---:|---:|---:|
| Five | 27 | 11,664 | 3,888 | 0 | 0 |
| Six | 30 | 12,960 | 4,320 | 0 | 0 |

Across the full length-five run, donor dispositions were four complete
certificates, six budget-exhausted checks, 36,710 no-export-trigger checks,
and zero unsupported checks. Length six had the same four certificates and
six budget-exhausted checks, 49,670 no-export-trigger checks, and zero
unsupported checks. The six exhausted checks remain on four-bus cycles, not
on the newly added loops. All control checks are inconclusive without any
certificate; their accepted status comes from the independent retained audit.

## Method, preservation and verification

The unchanged multigraph checker supports separate parallel-branch ratings,
losses and angle intersections, plus fixed transformer taps and phase shifts.
No physical input, battery pin, SoC policy, interval precision, trigger, audit
gate or numerical solver setting changed. The only new code is a non-solving
driver that calls the existing back-test and losslessly compacts its output.
Cycle catalogues are shared rather than repeated, and per-hour status strings
are run-length encoded; all certificate leaves and inconclusive budget records
remain in the selected evidence.

128 tests passed across the long-loop evidence, historical replay, multigraph,
automatic/preflight, interval arithmetic, slice and feasibility suites. New
driver and regression files pass Ruff lint/format checks. Final preservation
checks again verified all 183 pre-existing files unchanged for each run, and
the entire length-five catalogue is a subset of the length-six catalogue.

An independent NetworkX 3.6.1 bounded-cycle enumeration agreed exactly with
the entire simple-cycle catalogue through six buses: 23 triangles, 28
four-bus cycles, 27 five-bus cycles and 30 six-bus cycles. This verifies discovery
coverage, not feasibility. Its source/topology identity is retained separately
in `LONG_LOOPS_TOPOLOGY_VALIDATION.json`.

Both back-tests verified all 183 files present in the four analysis experiment
directories before each launch, excluding Python bytecode caches, unchanged
at completion. Existing transfer/recovery manifests, resources and historical
source/input evidence were additionally checked by the unchanged replay
machinery. No old artifact was overwritten or solve relaunched; no staging
or commit occurred. The runs took approximately 203 and 288 seconds of elapsed
read-only analysis time, respectively, and ran concurrently in separate Python
processes. Those durations are not new supervised OPF worker effort.

Selected evidence is separate:

- `LONG_LOOPS_5_EVIDENCE.json`
- `LONG_LOOPS_6_EVIDENCE.json`
- `LONG_LOOPS_TOPOLOGY_VALIDATION.json`

This remains retrospective scientific consistency testing, not holdout
predictive validation, complete infeasibility detection or global optimality.
Source E3/E3+ qualifications and historical Stage D remain untouched.

## Reproduce without optimization

```bash
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --extra dev python \
  -m experiments.tracy_socp_transfer_preflight.long_loops --max-cycle-length 5

UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --extra dev python \
  -m experiments.tracy_socp_transfer_preflight.long_loops --max-cycle-length 6
```

These commands print new evidence to stdout and preserve existing artifacts.
