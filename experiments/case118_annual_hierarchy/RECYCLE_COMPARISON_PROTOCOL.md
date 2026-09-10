# Pre-S3 bounded worker-recycling comparison protocol

This protocol is frozen before any comparison worker executes. It implements the
approved revision-8 plan in `plans/recycle-comparison-before-s3.md` without
changing the frozen S2 driver, runner, archive, schema, fixture, policy, or
solver configuration.

## Scientific scope

This is an observational comparison, reviewed with the user after execution. It
compares one uninterrupted worker with workers deliberately recycled every 32
or 16 completed intervals. It reports memory behavior, restart overhead,
trajectory residuals, and warm-start evidence. It does not automatically select
a production recycling interval or authorize S3.

Restart cadence is the sole intentionally varied factor. Arms run serially in a
fixed order, so uncontrolled machine-history and order effects remain
observational limitations.

## Frozen model and reference identities

Every arm uses `s2_fixture.load_s2_fixture()` and the unchanged
`frozen_p0_policy()` / `frozen_p0_solve_config()` over the original 168-hour S2
problem. The observer stops the study at completed boundary 64; this does not
truncate or otherwise modify the outer problem.

Frozen identities are:

- historical model/streaming fingerprint:
  `b62be721077ee5b5a3c61c93211197abeaf4e1ab5dabc2bc7d76b86b3520f4fd`;
- scenario SHA-256:
  `f602d67563d35e62df03cc716f82f0c3ba823813d0719c623b12a727f92ae12b`;
- policy SHA-256:
  `2186334bd2e7be3760636f0b20575c81deaff5f293fb9a725270157379957520`;
- solve-configuration SHA-256:
  `bfb818de03ddbfd983bb02def3aa3c51d0e6c1b075486ec66bca3035d82e2977`;
- shared outer artifact: `reference/outer-plan.json.gz`, 717,010 bytes,
  SHA-256
  `b4ac7b18b6e913e96991d5bbe217b01462c81be5b55587cbad17b2ca589d0b90`;
- S2 reference checkpoint SHA-256:
  `7e34a0ab1f00db2bfb164d1f9d6765231fdce8cd1a0f91f66dbc8230df546685`;
- tracked `reference/s2_first64_reference.json` SHA-256:
  `174f76ae6f9726c8eb8b1953faf1b6282fc56ef6fc8ad03143090c23828eb979`.

The comparison implementation fingerprint is computed at execution start from
the single ordered registry in `run_recycle_comparison.py`. It is recorded in
run contexts and results but is not used to validate the historical outer plan.

## Arms and lifecycle

Arms execute serially in this fixed order:

1. `never`: no planned restart;
2. `recycle_32`: restart at completed boundary 32;
3. `recycle_16`: restart at completed boundaries 16, 32, and 48.

Boundary 64 is always `study_complete`, never a recycle.

Before an arm's first invocation, the tracked outer plan is copied once into the
fresh arm trajectory directory and checked by byte count and SHA-256. It is
verified in place before every later invocation and is never overwritten or
repaired.

Every invocation uses `resume=True`:

- the first invocation has the seeded outer plan and no checkpoint, taking the
  verified `recovered_outer_without_checkpoint` path;
- subsequent invocations have the same seeded outer plan and a verified
  checkpoint from the prior worker.

The observer returns `planned_recycle` only at the arm's next future planned
boundary, and `study_complete` at boundary 64. A reviewed interruption resume
continues to the next future planned boundary; passed boundaries never retrigger.
A planned stop is distinct from every resource or worker failure.

## Resource supervision

Each worker runs in a fresh process session under an external supervisor.
Limits are:

- worker-process RSS: 24 GiB;
- no newly published checkpoint: 60 minutes;
- cumulative retained wall time per arm: 4 hours;
- cumulative retained wall time across all arms: 12 hours;
- RSS and checkpoint polling interval: no longer than one second.

Wall accounting includes process launch and planned-restart overhead across all
invocations in the retained arm directory. Discarded effort is reported
separately. Post-execution analysis time is reported but does not count against
execution ceilings.

Unexpected termination is classified distinctly as `rss_limit`,
`total_wall_limit`, `checkpoint_stall_limit`, `worker_failure`,
`artifact_failure`, `outer_failure`, `recovery_exhausted`, or
`provenance_mismatch`. It stops the arm without automatic continuation.

## Restart and memory measurements

Worker peak RSS is the maximum external sample for that invocation.
Safe-boundary RSS is the archived in-process `after_release` sample, selected by
phase. Other phases remain separate diagnostics.

The compact record retains external first/peak/final RSS for every invocation
and the complete `after_release` series indexed by global interval. Recycled
arms are compared with `never` at identical global intervals so different
physical windows cannot masquerade as a recycling effect.

For a resumed worker, `restart_to_first_checkpoint_seconds` begins at child creation and ends at the
supervisor monotonic timestamp of its first polling observation of a checkpoint
that:

1. has `completed_intervals == restart_boundary + 1`;
2. differs in SHA-256 from the pre-invocation checkpoint; and
3. passes full existing checkpoint verification.

The polling interval is retained so observation granularity is explicit. This
quantity includes startup, validation, the first AC solve, publication, and
polling latency. Analysis reports the corresponding uninterrupted `never`
interval duration and their difference as an estimate of incremental restart
cost; it does not label the full duration as pure restart overhead.

## Correctness evidence and comparisons

Existing P0/M17 tolerances remain unchanged execution and archive-validation
inputs. The comparison introduces no execution-affecting tolerance and no
automatic pass/fail or S3 advancement gate.

Analysis reports raw cross-run residuals for executed storage power, realized
SoC, causal-source arrays, assigned starts, and solver starts. Numerical start
comparisons include maximum absolute and normalized differences by named
variable group, with original and reduction-introduced IPOPT coordinates
reported separately. Normalization uses `max(1, max(abs(reference)))` for each
declared group; it is descriptive and never an execution gate. Existing physical
tolerances may be displayed as descriptive context where units match; observed
differences are not hidden by loosening a threshold.

Categorical and structural evidence is checked exactly: artifact hashes,
interval indices, device identities and order, attempt identities and ordinals,
source labels, layout keys/shapes/order, coordinate counts, and finite values.
At each planned restart boundary, analysis separately reports:

- causal correctness of the preceding accepted attempt;
- consistency of assigned and actual solver-start evidence; and
- cross-run residuals against `never` and the tracked S2 reference.

The tracked tiered S2 extract contains trajectory evidence for intervals 0..63,
boundary-zero SoC, and full actual-start evidence at boundaries 0, 16, 32, and
48. Its sidecar binds its exact bytes. Regeneration additionally requires the
ignored S2 source archives and validates them through the existing complete
checkpoint/schema chain.

## Continuity and partial outcomes

A completed arm contains intervals exactly 0..63 once each. A valid partial arm
contains exactly one contiguous prefix 0..`k-1`, without gaps or duplicates.
For either outcome, analysis verifies immutable checkpoint extension, resource
evidence continuity, unchanged outer identity, stable storage identity, SoC
handoff at process boundaries, and the preceding-controller chain.

A partial trajectory is retained as an explicitly partial scientific record. It
is not silently promoted to a complete comparison and does not trigger an
automatic rerun.

## Tracking boundary

Execution writes only ignored artifacts beneath
`results/recycle_comparison/<arm>/`. No tracked file changes while workers run.
After all workers exit, independent analysis may deliberately promote one
compact tracked `RECYCLE_COMPARISON_RESULTS.json` at the experiment root. Raw
windows, checkpoints, logs, resource samples, supervision records, and
provisional summaries remain ignored.

## Operational commands

Before the expensive run, verify the tracked reference and focused implementation
suite:

```bash
uv run python -m \
  experiments.case118_annual_hierarchy.reference.extract_s2_reference
uv run --extra dev pytest \
  tests/test_case118_recycle_reference.py \
  tests/test_case118_recycle_comparison.py \
  tests/test_case118_recycle_analysis.py
```

Reference regeneration additionally requires the ignored S2 source archives:

```bash
uv run python -m \
  experiments.case118_annual_hierarchy.reference.extract_s2_reference \
  --regenerate
```

The comparison implementation and reference artifacts must then be committed,
and `git status --porcelain` must be empty. Start one fresh serial comparison
with:

```bash
uv run python -m \
  experiments.case118_annual_hierarchy.run_recycle_comparison
```

This command requires the default ignored output root to be absent. It creates
fresh arms in frozen order and continues automatically only after a verified
`planned_recycle` outcome.

After explicit user review of an interruption, continue one retained arm with:

```bash
uv run python -m \
  experiments.case118_annual_hierarchy.run_recycle_comparison \
  --resume-arm recycle_16
```

Replace the arm name as appropriate. Reviewed resume verifies the checkpoint,
outer plan, prior committed identities, stale-process state, and cumulative wall
allowance before launch. It never kills an apparently live prior process and
never automatically follows an abnormal outcome.

Reconstruct compact observational results without writing a tracked file:

```bash
uv run python -m \
  experiments.case118_annual_hierarchy.recycle_analysis
```

Only after reviewing that reconstruction, deliberately create the immutable
tracked compact result with:

```bash
uv run python -m \
  experiments.case118_annual_hierarchy.recycle_analysis --promote
```

Promotion refuses an empty result set and refuses to overwrite an existing
`RECYCLE_COMPARISON_RESULTS.json`. Neither analysis command performs an OPF run
or makes an automatic S3 advancement decision.

The compact result also retains per-invocation wall time, checkpoint identities,
polling resolution, and start/end execution contexts, including the execution
commit, platform, architecture, physical memory, Python/package/IPOPT versions,
and both source fingerprints. Raw supervision files remain integrity-bound but
are not required to recover this ordinary scientific provenance.
