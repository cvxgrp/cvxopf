# S4 annual outer-plan protocol

This protocol is frozen before the authoritative S4 solve. S4 constructs,
solves, audits, and archives the exact 8,760-step lossy-DC outer plan for the
primary rated Case118 annual scenario. It does not construct or execute an AC
window. The five-minute AC fallback and shard execution are therefore outside
S4 and remain gates for S4b/S5.

## Frozen scientific problem

- converted, provenance-checked rated PGLib-OPF Case118 network;
- all 8,760 hourly active-load, reactive-load, and renewable-availability rows
  from the deterministic annual scenario, without slicing or wrapping;
- 15% annual available renewable energy;
- aggregate storage power equal to 5% of annual peak load;
- four-hour storage duration at buses 41, 65, 89, and 105;
- each storage device initially at 50% SoC and constrained to return to 50% at
  annual boundary 8,760;
- enforced branch ratings, explicit generator/load/device fleets, and no load
  shedding; and
- the exact frozen CLARABEL outer options, construction options, identities,
  tolerances, and one-hour timestep used by the hierarchy.

`s4_fixture.load_s4_fixture()` is the sole materialization path. It validates
the complete case, input arrays, identities, policy, solver configuration,
timestamps, and combined scenario hash before model construction.

## Outer-only equivalence gate

S4 uses `streaming_runner.solve_frozen_outer()` with the same immutable input
snapshot and policy boundary used by the streaming hierarchy. Before annual
execution, a characterized 24-hour fixture must compare this seam with the
public hierarchical controller's retained outer plan on:

- formulation identity and exact scalar variable, scalar equality, and scalar
  inequality counts (reported separately from result-array dimensions);
- storage and boundary identities;
- accepted status/classification and objective;
- complete public result arrays and boundary SoC trajectory;
- residual audit and terminal obligation; and
- model, policy, solver, and input fingerprints.

The retained equivalence record contains canonical result, boundary, and
structure digests for both sides; complete result schemas; objectives;
storage/boundary identities; both residual mappings; and the common input,
policy, and solve-configuration fingerprints. The public M17 outer audit omits
the streaming probe's inactive `branch_mw_abs` diagnostic. Equivalence may
project a zero public value only after asserting the public key is absent and
independently requiring the streaming value to be zero.

The public comparison may stop before AC construction. A standalone similar
lossy-DC model is not equivalent evidence.

## Resource and supervision contract

The authoritative output directory is
`experiments/case118_annual_hierarchy/results/s4_annual_outer_rated` and must
not exist at launch. One supervising parent starts one fresh worker process and
samples current child RSS every second.

Frozen limits on the S0/S3 workstation are:

- child current RSS: 16,384 MiB;
- worker wall time: 7,200 seconds;
- total supervisor wall time: 10,800 seconds; and
- polling interval: 1 second.

The worker records current RSS and elapsed time before construction, after
construction, before solve, after solve, after archive, and after live-build
release. The supervisor separately retains first, peak, and final sampled RSS.
Construction, canonicalization/solve, audit, serialization, release, and total
wall time are reported separately where the existing interfaces expose them.

Crossing a resource limit terminates the worker and produces an explicit
`rss_limit`, `worker_wall_limit`, or `total_wall_limit` supervision outcome.
Worker-launch or construction failures, solver exceptions, certified
infeasibility, unusable primals, residual rejection, artifact failure, and
provenance mismatch remain distinct. No failure is reclassified as
mathematical infeasibility.

If multiple limits are observed in one poll, all are retained and the primary
classification uses the frozen priority RSS, worker wall, then total wall. An
accepted resource gate requires at least one successful external RSS sample
and a finite positive peak; missing RSS evidence is
`resource_measurement_failure`, not a zero-memory observation.

There is no scientifically valid mid-solve checkpoint. An abnormal outcome
stops automatic execution and retains all reached evidence for review. Any
approved rerun starts the complete annual outer problem from scratch in a new
explicitly labeled attempt directory; it never overwrites or calls itself a
resume.

## Artifact layout and publication order

The output directory contains:

- immutable pre-worker execution context and frozen configuration;
- an ephemeral active-worker marker;
- append-only worker log;
- immutable worker result retaining every reached phase and classification;
- immutable accepted `outer-plan.json.gz`, written only after the complete
  outer audit passes;
- immutable supervision record and replaceable latest-supervision pointer; and
- no AC window, attempt, trajectory checkpoint, or executed-action artifact.

The worker result is published before supervision finalization. An accepted
outer archive is written and hash-verified before the worker reports success.
Existing immutable targets cause a controlled artifact failure rather than
replacement. The active marker is removed only after durable supervision.

After execution, independent analysis reloads and semantically validates the
outer archive, reconstructs the complete residual audit and terminal boundary,
checks every artifact/provenance hash, and promotes compact `S4_RESULTS.json`
immutably. Promotion retains a separate clean Git commit and source fingerprint
for the analysis implementation, distinct from execution provenance. Raw
outputs remain ignored.

## Advancement gate

S4 can advance to S4b only when:

1. fixture and 24-hour outer equivalence gates pass;
2. the worker and supervisor contexts match the committed clean source;
3. CLARABEL returns an eligible accepted primal;
4. independent residual, identity, terminal, and result/signpost checks pass;
5. resource limits are respected;
6. exactly one immutable annual outer archive exists and no AC work exists;
7. compact analysis is reviewed and promoted; and
8. the accepted outer signposts are then used—without AC-outcome tuning—to
   freeze the S4b shard manifest.

An incomplete or resource-boundary result remains useful scaling evidence but
does not authorize S4b.
