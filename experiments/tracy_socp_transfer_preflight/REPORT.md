# SOCP transfer preflight: retained Tracy back-test

## Result

The report-only preflight identified local infeasibility certificates for
exactly the three problematic donor schedules, each at global hour 2452.
It produced no certificate against the 15 independently replayed compatible
transfers or the six retained accepted AC control schedules.

Generic SOCP non-tightness/recovery warnings were not discriminative: all six
SOCP donor points were flagged, including the five whose battery schedules
had audited-compatible AC transfers. Thus a point warning must not be used
as a schedule-rejection rule.

No OPF optimizer was called. Production sources, physical constraints,
battery pins, original endpoints, preparation policies, solver settings and
all completed numerical-run artifacts remain unchanged. Historical labels
are preserved separately from the new diagnostic verdicts.

| Historical outcome | Retained schedules | Certified incompatible | No certificate |
| --- | ---: | ---: | ---: |
| Audited-compatible AC transfer | 15 | 0 | 15 |
| Native numerical rejection | 2 | 2 | 0 |
| Timeout, no finalized native point | 1 | 1 | 0 |

The last two historical labels were not themselves infeasibility evidence.
The separately computed loop certificates now establish modeled incompatibility
for those particular schedules. A timed-out candidate was never accepted or
treated as a completed numerical solution.

## Implementation

`preflight.py` exposes two report-only routines:

- `point_screen(build, point, first_hour)` independently audits a retained
  SOCP primal and uses the existing forest-recovery diagnostics. It reports
  hourly cone slack, complete fundamental-cycle consistency, short-cycle
  localization, reconstructed branch loading, voltage-bound activity and
  source constraint failures. It does not change the build's status or values.
- `screen_schedule(kwargs, schedule, first_hour, targets, max_boxes=64)` takes
  numeric target AC inputs and fixed battery powers, with no historical outcome
  labels. It checks the original battery/energy/endpoints and the bounded local
  loop conditions. It does not construct or solve an hourly OPF.

The CLI back-test combines these routines with independent native replay of
completed transfer evidence. There are three schedule verdicts:

| Verdict | Meaning |
| --- | --- |
| `audited_recovered_witness` | A complete independently audited recovered AC point preserves the pins and original energy/endpoints; not a new optimizer acceptance or dispatch authorization. |
| `certified_incompatible` | Necessary local conditions exclude all permitted voltages at one hour, ruling out the full schedule under the matched model. |
| `unresolved` | No witness or certificate was obtained. This does not override a separately available historical AC witness. |

No direct recovered SOCP witness was found in this back-test. The preflight
alone consequently leaves the other 15 schedules unresolved; their separate
historical accepted AC witnesses still establish compatibility. Passing or
not triggering this limited certificate screen is never called feasibility.

### Generic point screens

Each retained 24-hour SOCP point has 179 voltage-product edges. All 62
fundamental-cycle residuals are checked, supplemented by 51 distinct simple
three-/four-edge cycles for localization. Cycle orientations are canonical,
errors are wrapped modulo 2π, and phases below the existing phase floor remain
undefined rather than being assigned fabricated angles.

The existing `SOCPAuditTolerances` rank and cycle thresholds are used without
fitting new thresholds to the retained outcomes. Every SOCP point had warnings
at all 24 hours: 144/144 screened source hours. Five of these six donor battery
schedules nevertheless had compatible transfers. Edge/cycle defects establish
nonrecoverability of a particular saved point, not impossibility of finding
another AC point with the same battery powers and different free controls.

At hour 2452, the known 71–72–24–70 cycle misses angle closure by about 39.55°
despite essentially tight edge cones. The preflight also retains larger cone
slack elsewhere; those warnings are not the local certificate.

### Battery-conditioned certificate screen

The certificate implementation is deliberately the existing, versioned Tracy
71–72–24–70 loop with radial 71–73. It is not a generic network infeasibility
solver. Original thermal limits, charging, voltages and device bounds enter
the necessary inequalities. Other real/reactive dispatch, renewable service
and shedding are allowed to vary over their original feasible sets.

All 432 donor-hour slices were examined, plus 144 AC-control-hour slices.
For donor hours:

- 423 did not require positive exports on both legs of this directional template;
- six positive-export checks exhausted their bounded screen without a certificate;
- three were completely excluded, all at hour 2452.

The root probe and any subsequent interval search together consume at most
64 tested boxes per triggered hour. A missing template assumption, such as
an unrated local branch or an added load at bus 71, returns non-applicability,
not a certificate. Applicability, missing phase, budget exhaustion and absence
of the directional trigger are all preserved as non-success outcomes.

| Donor, deficit-to-surplus neutral window [2439,2463) | Original outcome | Certified hour | Export 71→72, MW ≥ | Export 72→24, MW ≥ |
| --- | --- | ---: | ---: | ---: |
| SOCP | Timeout | 2452 | 123.457010726 | 130.525763019 |
| Lossy DC | Native rejection | 2452 | 123.457010717 | 127.294728342 |
| Copper plate | Native rejection | 2452 | 114.019772151 | 114.538027136 |

The SOCP and lossy-DC certificates each exclude a single full-domain voltage
box. Copper plate needs ten terminal boxes, reached in 19 checker visits
(20 total visits including the preliminary root probe). The original accepted
AC controls yielded zero contradictions across all six 24-hour schedules.

## Back-test limitations

This is a retrospective consistency check. The certificate template was
selected after inspecting hour 2452; these are not held-out prediction results.
We did not tune point-warning thresholds or claim general sensitivity,
specificity, or global completeness of the local certificate method.

The results support a practical workflow: use cone/cycle warnings to localize
possible obstructions, then seek a schedule-conditioned certificate before
spending numerical retries. They do not support rejecting a battery schedule
solely because its SOCP primal cannot be directly recovered.

All source E3/E3+ dispositions remain untouched. In particular, the forced-
shedding SOCP source retains classification `rejected` and its failed original
relaxation audit. Its owner-approved schedule remains usable for this diagnostic;
all three forced-shedding transfers have separately audited compatible AC
results. A successful transfer does not retroactively accept its donor point.

## Provenance, endpoint fix and verification

The original transfer matrix contains 18 finalized launches, 17 completed
native/result bundles and one timeout without a completion manifest. Existing
historical checks verify requests, resource journals, manifests, supervision,
source identities and their hashes. Completed records are independently
reconstructed, their canonical starts compared, and native primals restored
and audited without optimizing. The immutable historical file inventory is
hashed before and after the back-test; the inventories match exactly.

Historical contexts retain their original commit identities. The reader permits
only the known three recovery-only script differences and newly added analysis
files in the explicitly scoped experiment directories. All historically bound
mathematical sources, numerical packages, protocol and transfer code must still
match. This permits committing report-only work without pretending the old solve
ran at a new HEAD or rewriting its binding.

The owner-identified Taylor remainder endpoint issue was fixed in
`../tracy_battery_transfer_loop/analyze.py`: exact `error.hi.copy_negate()` replaces
ambient-context `-error.hi`. Three regressions inspect the actual remainder
endpoints at Decimal precisions 7, 28 and 60, and compare the resulting atan
enclosure against independent exact-rational series bounds. All pass.

The pre-fix source and evidence were preserved under the loop experiment's
ignored `results/endpoint-negation-before-Qb92BN`. Its original `EVIDENCE.json`
was not overwritten. New `EVIDENCE_ENDPOINT_FIX.json` records lineage and the
corrected source digest. Replaying the loop certificate produced identical
exclusions, partition counts and reported margins. The preflight now binds this
corrected version explicitly.

Verification includes cycle orientation and uniqueness, exact-product cycle
closure, phase defects with unchanged magnitudes, undefined phases, optimizer
blocking and restoration, conservative unknown categories, exhausted-budget
behavior, template applicability, original pins/endpoints, unchanged input
arrays, historical context guards and the complete selected evidence. The
focused preflight/loop/single-hour suite passes (46 tests, none skipped);
lint and format checks pass.
No production source was changed, and all changes remain unstaged.

## Reproduction

From the repository root:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
VECLIB_MAXIMUM_THREADS=1 UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache \
uv run --extra dev python -m experiments.tracy_socp_transfer_preflight.preflight --backtest

UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --extra dev python -m pytest \
  experiments/tracy_socp_transfer_preflight/test_preflight.py \
  experiments/tracy_battery_transfer_loop/test_analysis.py \
  experiments/tracy_battery_transfer_slice/test_slice.py -q
```

The CLI emits evidence to stdout and has no execution mode. `EVIDENCE.json`
contains the selected complete back-test report, including per-hour diagnostics,
certificate leaves, historical labels, source digests and summary counts.
The reusable `screen_schedule` routine can run before a transfer attempt;
it never uses that attempt's future numerical outcome as an input.
