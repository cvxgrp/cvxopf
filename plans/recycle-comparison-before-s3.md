# Bounded worker-recycling comparison (pre-S3)

Status: **Complete.** The approved revision-8 protocol was implemented at
checkpoint `b50755e`; the authoritative comparison completed on 2026-08-24.
All three 64-interval arms completed and the promoted observational record is
`experiments/case118_annual_hierarchy/RECYCLE_COMPARISON_RESULTS.json`.

This revision incorporates the scientific-completeness review, resolves the
outer-plan/reference design against the actual streaming architecture, and
folds in the previously-open decisions (observational numerical reporting, wall ceilings,
negative-control test). Revision 4 corrected three implementation-critical
points (all arms `resume=True` with checkpoint-free first invocation; two
separate provenance fingerprints; reproducible tracked S2 reference). Revision
5 implements the re-re-review: (1) S2's ~6/~17.5 GiB bands are labeled
historical context, with the contemporaneous `never` arm as the memory
reference, because all new workers take the checkpoint-free outer-*load* path
rather than S2 invocation 0's in-process outer-*solve* path; (2) the outer seed
is copied once per fresh arm and thereafter verified in place, never
overwritten; (3) restart-overhead endpoint is defined as the first *new*
verified checkpoint at `restart_boundary + 1` with a changed hash; (4) warm-
start evidence now separates causal correctness, start-construction correctness
(`assigned_start` / `solver_x0` / layout), and cross-run agreement, and the
reference extract carries those actual-start fields plus boundary 0; (5) the
extract's own hash lives in a tracked sidecar; (6) the comparison fingerprint
is an explicit ordered path registry hashed like S2's, and Section 3 records
both fingerprints; (7) interruption-resume semantics are reconciled with the
global planned-boundary schedule. Revision 6 implements the third re-review:
(1) the tracked compact result moves to the experiment root because the
existing `results/` ignore rule cannot be unignored by a nested `.gitignore`
(confirmed with `git check-ignore`); (2) execution writes only ignored
artifacts and a post-run analysis phase promotes the tracked result, so the end
worktree stays clean; (3) the protocol holds only the fingerprint *registry
definition*, and the derived value is computed at execution start and recorded
outside the protocol, removing circularity; (4) the reference extractor
*validates* the S2 source via existing checkpoint/schema validators before
extracting; (5) two stale statements (the extract "carries its own SHA-256" and
the numerical-comparison basis) are corrected to match the sidecar and Section 7; and
(6) the full historical fingerprint is used where it is an operational constant.
Revision 7 implements the fourth re-review: (1) the first-64 reference becomes a
**tiered** extract - compact trajectory/metadata for all 64 intervals, full
actual-start evidence only at restart-relevant boundaries 0/16/32/48 - because
retaining full starts everywhere would be ~50 MB *(verified against the S2
archives)*, larger than the 19.1 MiB of raw windows it summarizes; (2) Section 7
defines actual-start comparison semantics (exact equality for structure/layout,
nonfinite rejection, per-named-variable-group absolute and normalized tolerances
with per-boundary residuals); (3) the fingerprint path registry gets a single
operational owner in `run_recycle_comparison.py` with Section 10 tests; (4)
Section 11 specifies cumulative wall accounting across invocations, recycles,
and reruns; and (5) the reference extractor distinguishes generation mode (needs
ignored S2 archives) from clean-checkout verification mode. Revision 8 applies
the final, observationally scoped review: (1) restart cadence is identified as
the sole intentionally varied factor without claiming that fixed order removes
all machine-history effects; (2) archive continuity distinguishes a complete
0..63 trajectory from a valid contiguous partial prefix; and (3) restart
overhead ends at the supervisor's first polling observation of a qualifying new
checkpoint, subject to successful verification, with the polling interval
recorded. Facts marked *(verified)* were confirmed empirically against the
committed S2 artifacts at current commit `683758f`.

## Purpose

Before the S3 one-month hierarchy, the case118 annual-hierarchy plan requires a
frozen worker-recycling policy or a bounded recycling comparison. We take the
comparison form (Approach B): turn S2's single uncontrolled, crash-driven
process restart into a small set of deliberate, measured planned restarts, and
check that warm-started AC execution is numerically and causally invariant
across a process memory reset.

This is an observational study reviewed live by the user. We run it, report
predefined measurements, and decide together how to proceed. No self-imposed
pass/fail gating.

## Completed result

The comparison completed 192/192 accepted controlling intervals. The
`never`, `recycle_32`, and `recycle_16` arms completed in 3,396.5, 3,392.2,
and 3,391.8 seconds, with maximum externally sampled RSS of 20,025.5,
19,501.7, and 17,926.3 MiB. All planned restarts resumed the exact verified
checkpoint. Executed storage power, realized SoC, attempt labels, causal-source
state, and reconstructed actual starts agreed across treatments at the
predeclared comparison points; the full action and SoC trajectories matched
exactly.

The result is observational and retains `automatic_advancement_gate: false` as
designed. It supports reviewed selection of a bounded recycling cadence for
S3; it does not itself promote a universal default or diagnose the source of
process-memory pressure.

## What S2 showed (grounding evidence)

Reconstructed from the committed S2 `resource-samples-*.json` chunks and their
tracked `elapsed_seconds` (throwaway probes, since removed):

- **Invocation 0** (intervals 0-128, one long-lived worker): ramps to ~7.4 GiB
  by interval 1, then holds a per-interval `after_release`/`after_ac_build`
  peak near **~6,030 MiB** from ~interval 45 through 127. Per-interval wall:
  median ~54 s, min ~21 s, max ~1,253 s (one outlier window). At interval 128
  an `after_archive` sample jumps to 13,341 MiB; run_s2's 1 Hz supervisor
  observed 19,220 MiB and terminated (`resource_limit`).
- **Invocation 1** (intervals 129-168, fresh crash-resumed worker): starts at
  **182.6 MiB**, climbs to a **~17,500 MiB** band and stays there to 168.
  Per-interval wall: median ~40 s, max ~239 s.

The two workers reached different memory bands (~6 GiB vs ~17.5 GiB) on the same
code, physics, and window mechanics. A linear fit across the restart
("~93 MiB/interval") is an averaging artifact of that discontinuity and is not
reported as a growth rate.

**Observational question:** does a *planned* restart return the worker to the
lower band or reproduce the elevated band seen after crash recovery? We observe
and report; we do not root-cause it here.

**Historical-versus-new memory caveat (do not overinterpret).** S2 invocation 0
*solved* the outer plan in-process and released it before AC execution; S2
invocation 1 *loaded* the archived outer plan on resume. Every worker in this
study - including the `never` arm's first worker - uses `resume=True` and
therefore takes the checkpoint-free *outer-load* path, not the in-process
outer-solve path. So the new `never` worker is **not** a process-memory
replication of the historical ~6 GiB worker and may resemble the historical
resumed band even with zero planned recycles. Consequently:
- S2's ~6 GiB and ~17.5 GiB bands are **historical context**, not expected
  classifications for the new workers;
- the controlled memory comparison is **among the three new arms**, whose first
  workers share the same checkpoint-free outer-load path;
- the **`never` arm is the contemporaneous memory reference**;
- S2 first-64 remains the external **trajectory** reference only, never an
  equivalent process-memory reference.

## 1. Frozen inputs and exact reference artifacts

- **Fixture:** `s2_fixture.load_s2_fixture()`, unchanged. Horizon 168, delta 1 h,
  four ideal storage devices at 50% initial SoC, global 50% terminal obligation
  at boundary 168, fixed nonsheddable loads, enforced AC `rateA`. Scenario hash
  `f602d675...`.
- **Policy / solve config:** `frozen_p0_policy()` / `frozen_p0_solve_config()`,
  the exact S2 hashes (`P0_EXPECTED_POLICY_SHA256`,
  `P0_EXPECTED_SOLVE_CONFIG_SHA256`).
- **Reference outer artifact:** the tracked comparison-owned copy
  `reference/outer-plan.json.gz` (byte-identical to the S2
  `results/s2_week_rated/trajectory/outer-plan.json.gz`; 717,010 bytes; SHA-256
  `b4ac7b18b6e913e96991d5bbe217b01462c81be5b55587cbad17b2ca589d0b90`; historical
  source fingerprint `b62be721...`; written at commit `3cd4229`). *(verified:
  loads and re-validates clean under a fresh S2 fixture at current commit
  `683758f`; `accepted_primal=True`; 169 boundaries 0..168; streaming source is
  byte-identical between `3cd4229` and `683758f`.)*
- **Correctness reference trajectory:** the accepted S2 sequential first-64
  prefix. Because this study reuses the *same* 168-h problem and the *same*
  outer plan, S2's actions over global intervals 0-63 are the correct external
  reference. The S2 window archives (`trajectory/window-*.json.gz`) supply
  executed storage power, realized SoC, controlling ordinal/ID, and causal
  source per interval. Because those archives are Git-ignored, the study tracks
  a compact deterministic reference (see below) rather than depending on a
  local working copy.

### Reproducible-from-clean-checkout policy

The S2 outer plan, checkpoint, and window archives exist only in the local
working copy and are Git-ignored; `S2_RESULTS_METADATA.json` tracks their hashes
but not their bytes. To make the exact reference obtainable from a clean
checkout, the study tracks two comparison-owned artifacts:

1. **`reference/outer-plan.json.gz`** - a byte-identical copy of the S2 outer
   artifact (717,010 bytes, SHA-256
   `b4ac7b18b6e913e96991d5bbe217b01462c81be5b55587cbad17b2ca589d0b90`).
2. **`reference/s2_first64_reference.json`** - a compact, deterministic,
   **tiered** extract of the S2 first-64 prefix. Retaining full actual-start
   evidence for all 64 intervals would be ~50 MB of JSON *(verified against the
   S2 archives: ~580 KB at interval 0 and ~796-804 KB per later interval)*,
   larger than the 19.1 MiB of raw compressed window archives it summarizes and
   therefore not "compact." The schema is tiered to the actual scientific need:
   - **Tier A - all intervals 0-63:** executed storage power; realized SoC;
     controlling attempt ordinal and ID; warm-start `transformation` and
     `source_kind` / `source_attempt_id`; and the source window archive SHA-256.
   - **Tier B - restart-relevant boundaries only (0, 16, 32, 48):** the
     complete actual-start evidence Section 5 needs - `assigned_start`,
     `solver_x0`, `solver_x0_layout` / `layout_signature`, relevant
     `solver_evidence`, `structural_signature`, and the causal-source fields
     (`first_soc_mwh`, `first_b_mw`, `solution_values`). Boundary 0 is retained
     as the generated-flat baseline; 16/32/48 are the possible post-restart
     intervals under the arm schedule.
   - **Invariant deduplication:** structural information invariant across the
     retained boundaries is stored once, with per-boundary hashes proving
     correspondence rather than repeating the payload.
   Boundary 0's SoC is retained explicitly so the 65-boundary SoC comparison is
   reproducible without reconstructing boundary 0 from unstated fixture
   knowledge. This keeps the tracked reference to a few MB while preserving
   every quantity the analysis consumes. The extract binds the S2
   final-checkpoint hash
   (`7e34a0ab1f00db2bfb164d1f9d6765231fdce8cd1a0f91f66dbc8230df546685`).
   Its own SHA-256 is recorded out-of-band in a tracked sidecar
   `reference/s2_first64_reference.sha256` (standard `shasum` layout: the 64
   lowercase hex digits, two spaces, the filename, and a trailing newline) and
   in the protocol / compact result (a file cannot contain the hash of its own
   final bytes).

   **Validated extraction (not mere hashing).** A tracked
   `reference/extract_s2_reference.py` first *verifies* the S2 source using the
   existing validators - the frozen S2 fixture, policy, solve hash, historical
   source fingerprint, and outer artifact - and only then extracts the first 64
   records. Extraction establishes, in order: (a) the S2 final-checkpoint hash
   matches the tracked S2 metadata; (b) the checkpoint binds the complete
   immutable window prefix; (c) the first 64 window paths, sizes, and hashes
   match that verified checkpoint; (d) each selected archive passes existing
   schema validation; (e) the initial boundary and per-interval fields are
   taken from those validated records. It then serializes canonically,
   regenerates the extract deterministically, compares generated bytes to the
   tracked JSON, and verifies the sidecar hash. Binding the final-checkpoint
   hash in the output is necessary but not sufficient; the validation chain
   above is what proves extraction came from validated source data.

   **Two extractor modes.** A clean checkout has the tracked extract but not the
   ignored S2 source windows, so the extractor distinguishes:
   - **generation mode** (requires the local ignored S2 archives): runs the full
     validation chain (a)-(e) above, regenerates the extract, and compares to
     the tracked JSON and sidecar - used when (re)producing the reference;
   - **verification mode** (clean checkout, no raw S2 windows): verifies the
     tracked outer artifact, the reference JSON against its sidecar, and the
     source-window hashes and schema *recorded inside* the extract, without
     claiming to revalidate unavailable raw windows.

This avoids tracking the large S2 window archives. The tracked reference's
*identity* is verifiable anywhere (verification mode); regenerating its full
provenance chain requires the ignored S2 source artifacts (generation mode).

### Two provenance identities

The frozen S2 outer artifact is bound to the **historical model/streaming
fingerprint** `b62be721...`. The new comparison code has its own provenance.
Conflating them would make the shared outer plan fail verification. The study
therefore defines and records two distinct identities:

1. **Frozen model/streaming fingerprint** (operational constant
   `b62be721077ee5b5a3c61c93211197abeaf4e1ab5dabc2bc7d76b86b3520f4fd`): passed
   to the driver and used to validate the shared outer plan. Before execution,
   the study recomputes the S2 source registry (`s2_source_paths()` / the
   `src/cvxopf` tree) and requires it still equals
   `b62be721077ee5b5a3c61c93211197abeaf4e1ab5dabc2bc7d76b86b3520f4fd`; a
   mismatch halts the study because the reused artifact would no longer be
   model-consistent.
2. **Comparison implementation fingerprint:** a separate hash over an explicit,
   ordered path registry of every executable/text file that can affect the
   study - not merely files labeled "new": the comparison supervisor, the
   analysis, the reference extractor, the protocol document, the comparison
   tests, and any experiment-owned modules they import that lie **outside** the
   frozen S2 source registry. The hashing procedure mirrors S2's
   `s2_source_fingerprint` (sorted unique paths; for each, hash the
   repo-relative POSIX path, a NUL, the file bytes, a NUL). Modules already
   inside the frozen S2 registry are covered by identity 1 and are not
   double-counted. It never enters the driver's outer-plan verification.

   **No circularity.** The protocol document contains only the *registry
   definition* (the ordered path list) and the historical model fingerprint -
   never its own derived comparison-fingerprint value. The comparison
   implementation fingerprint is **computed at execution start** from the
   committed registry (the protocol bytes may safely be one of the hashed
   inputs) and its value is recorded in run contexts, supervision records,
   ignored provisional summaries, and the final tracked compact result - not in
   the protocol.

   **Single operational owner.** The ordered path list lives in exactly one
   place - a Python registry symbol in `run_recycle_comparison.py` - and is not
   duplicated as a second authoritative list elsewhere; the protocol documents
   the intended members and points at that symbol. Section 10 tests assert:
   every declared path exists; ordering is deterministic; the protocol document
   is included; every comparison-owned module imported from outside the frozen
   S2 registry is included; the tracked compact result is excluded; and the
   frozen S2 registry members are not double-counted.

This distinguishes unchanged model semantics from new experiment orchestration;
it does not bypass provenance.

## 2. Shared outer-plan strategy (Option B)

All three arms consume the **identical** S2 outer artifact by SHA-256 identity.
No arm solves its own outer plan, so outer-solver variability is removed and
restart cadence is the sole intentionally varied experimental factor. Fixed
serial order and uncontrolled machine-history effects remain observational
limitations.

**Seed-copy procedure.** The tracked reference outer artifact (Section 1) is
copied into the arm directory as `outer-plan.json.gz` **once per fresh arm,
before its first invocation**, and its size and SHA-256 are checked against the
tracked reference. **Before every later invocation the existing arm copy is
verified in place (size + SHA-256); it is never overwritten or recopied.** Any
mismatch - fresh or later - stops the arm as a hard error without replacement or
repair, because a later overwrite would needlessly mutate a trajectory directory
and could conceal accidental corruption. The driver additionally verifies the
artifact against inputs, policy, solve-config, source fingerprint, scenario
hash, and horizon on load.

**Startup semantics (implementation-critical).** A fresh trajectory run
(`resume=False`) rejects a directory that already contains `outer-plan.json.gz`.
Because every arm is seeded with the shared outer artifact, **all invocations
use `resume=True`**, distinguished by checkpoint presence:
- **First invocation of an arm:** `resume=True` with the seeded outer artifact
  and **no trajectory checkpoint**. The driver takes its documented
  checkpoint-free recovery path: it verifies the outer plan, starts from the
  fixture's initial SoC, and records phase `recovered_outer_without_checkpoint`.
  (The driver requires `outer.accepted_primal` on this path, which the S2 plan
  satisfies.)
- **Later invocation of an arm (after a planned recycle):** `resume=True` with
  the seeded outer artifact **plus** a verified trajectory checkpoint from the
  preceding worker.

The 64-interval boundary is a **study stop**, not a change to the 168-h problem:
the observer terminates each arm at global interval 64. The outer plan, terminal
obligation, and signposts are exactly S2's.

## 3. Arm order and execution environment

- **Arms and planned restart boundaries (inside the 64-interval study):**
  - `never`: 0 restarts; single worker; the internal + S2 external reference.
  - `recycle_32`: 1 restart at boundary 32.
  - `recycle_16`: 3 restarts at boundaries 16, 32, 48.
  - Boundary 64 is study completion, never a recycle.
- **Order:** fixed serial `never`, `recycle_32`, `recycle_16`. No concurrent
  experiment workers.
- **Machine provenance recorded:** OS, architecture, physical memory, CPU model,
  Python/CVXPY/NumPy/pandas/Clarabel/cyipopt/IPOPT versions, commit, clean
  worktree, **both** provenance fingerprints (frozen model/streaming and
  comparison implementation, Section 1), scenario/policy/solve hashes.
  Workstation to have no other known heavy jobs; environmental effects are not
  the object of study.
- **Output directories:** `results/recycle_comparison/<arm>/` (fresh per arm).
- **Interruption:** an interrupted arm may be resumed from its last verified
  checkpoint only under identical commit/fingerprint/scenario/policy/solve
  hashes; otherwise the arm directory is discarded and rerun. Interruption
  resume interacts with the planned schedule as follows:
  - review explicitly authorizes any interruption resume (it is not automatic,
    consistent with Section 4);
  - the planned-boundary schedule is defined on **global completed intervals**,
    not on invocation count;
  - already-passed planned boundaries do **not** trigger again; a resume at
    interval 20 in `recycle_16` runs on to the next *future* boundary (32),
    then 48, then 64 - it neither stops immediately nor re-recycles at 16;
  - the resumed worker stops at the next future planned boundary or at 64;
  - interruption-driven invocations are retained as their own recorded
    invocations and are **not** counted as planned-recycling events.

## 4. Planned-recycle lifecycle

Mechanism (no change to frozen S2, driver, runner, schema, or archive code):
`run_streaming_trajectory(..., resume=..., observer=...)` calls the observer at
each safe boundary (after durable persistence + build release) with a
`SafeBoundaryState.completed_intervals`. Returning a non-None reason terminates
cleanly with status `observer_terminated` and a verified checkpoint; `resume=
True` relaunches from it.

The comparison observer returns:
- `"planned_recycle"` when `completed_intervals` equals a recycle boundary for
  the arm and is < 64;
- `"study_complete"` when `completed_intervals == 64`;
- `None` otherwise.

Supervisor loop per arm:
1. launch fresh worker (`start_new_session=True`); record launch monotonic time
   and PID;
2. sample child RSS at <= 1 s;
3. on worker exit, read its result and the termination reason;
4. if reason is `planned_recycle` and completed < 64, relaunch with `--resume`
   after checkpoint verification;
5. if reason is `study_complete` and completed == 64, arm done;
6. any other termination (rss/wall/stall/worker_failure/artifact/outer/recovery)
   is a distinct recorded outcome and stops the arm **without automatic
   continuation**, pending review.

A planned recycle is a normal segment outcome, explicitly distinct from resource
termination. The supervisor waits for clean worker exit after observer
termination before resuming. A fresh process resumes only after checkpoint
verification succeeds.

**The external supervisor remains active throughout every invocation.** The
in-process observer owns only the planned safe-boundary decisions
(`planned_recycle`, `study_complete`). The external parent independently
enforces the 24 GiB RSS ceiling, the 60-minute no-checkpoint stall limit, and
the wall ceilings (Section 11), sampling child RSS at <= 1 s. The two paths are
complementary: the observer provides graceful planned stops between solves; the
parent provides protection when a solve runs long or memory crosses the ceiling
mid-solve. Observer-driven and parent-driven terminations are recorded as
distinct outcomes (Section 8).

## 5. Warm-start evidence at restart boundaries

The scientific point is that a planned memory reset is invisible to the physics
and the controller. Structural-signature equality alone only proves equivalent
model *structure*; it does not prove the correct preceding prediction crossed
the process boundary or that the actual solver start was rebuilt correctly. The
analysis therefore establishes three separate claims for the first controlling
interval **after each restart** (intervals 16, 32, 48 for `recycle_16`;
interval 32 for `recycle_32`), from the archived controlling attempt:

1. **Causal correctness** - the source came from interval `k-1`:
   - `transformation == "shifted_preceding"` and `source_kind == "attempt"`;
   - `source_attempt_id` is the accepted controlling attempt of `k-1`;
   - the archived `causal_source` reconstructs from that preceding attempt via
     `causal_source_from_archive` / existing schema validators (results
     retained, not re-implemented);
   - `causal_source` `first_soc_mwh` / `first_b_mw` / `solution_values` match
     `k-1`, with raw absolute and normalized residuals reported under Section 7.
2. **Start-construction correctness** - the actual start is the expected
   shifted/reconciled start:
   - the archived `assigned_start` equals the shifted start implied by the
     realized checkpoint SoC and the `k-1` prediction;
   - `solver_x0` and its `solver_x0_layout` / `layout_signature` are the
     expected layout for this window;
   - relevant `solver_evidence` (coordinate counts, layout signature) is
     consistent;
   - storage identity and `initial_soc_mwh` align with the realized checkpoint
     state carried across the process boundary.
3. **Cross-run agreement** - the actual starts are compared with the
   uninterrupted references (`never` arm and S2), reporting raw numerical
   differences for `assigned_start` and `solver_x0` and exact agreement for
   `structural_signature`.

Reported per restart boundary, not only as an aggregate.

## 6. RSS and restart-overhead definitions

- **Worker peak RSS:** max externally sampled child RSS over the invocation.
- **Safe-boundary RSS:** the in-process RSS sample tagged phase
  `after_release`, selected explicitly by phase from the archived resource
  samples, one per durable interval. If other phases (e.g. `after_archive`,
  `after_ac_build`) are also of interest they are reported independently by
  phase rather than merged into one "safe-boundary" number.
- **Late-worker summary:** median, min, max, IQR of safe-boundary RSS over the
  final N completed intervals of that worker (N = 8 for workers with >= 12
  intervals; for short 16-interval workers, report the final 4 and 8
  safe-boundary samples explicitly rather than claiming a steady state).
- We do **not** classify a worker as "6 GiB" or "17.5 GiB"; we report the
  summaries and inspect them together.

**Restart-to-first-checkpoint time** (per resumed worker):
- start: immediately after child creation (supervisor monotonic timestamp);
- baseline RSS: first successful external RSS sample;
- endpoint: the supervisor monotonic timestamp at its first polling observation
  of the first **new** checkpoint after resume (not solver return), accepted as
  the endpoint only after full verification succeeds. Because a checkpoint
  already exists before every resumed worker, the supervisor must distinguish
  old from new. The observed checkpoint must satisfy **all** of: (a)
  `completed_intervals == restart_boundary + 1`; (b) its SHA-256 differs from
  the pre-invocation checkpoint hash; and (c) full existing checkpoint
  verification succeeds. Plain file-existence polling is insufficient - it would
  match the preexisting checkpoint immediately. The supervisor records its
  polling interval so the observation granularity is explicit;
- operational time to progress = endpoint - launch; this includes startup,
  validation, the complete first AC solve, publication, and polling latency;
- optional decomposition from phase samples: launch+import; checkpoint/outer/
  archive verification; first resumed window build+solve; archive publication;
- baseline comparison: equivalent wall for the corresponding uninterrupted
  interval in the `never` arm. Their difference is reported as an estimate of
  incremental restart cost, not as a perfectly isolated causal measurement.

## 7. Numerical comparison quantities and reporting

All comparisons are device-aligned by explicit storage ID, with fixed interval
indexing, and reject nonfinite values.

Compared quantities (each arm vs the `never` arm, and `never` vs S2 first-64):
- executed storage power by interval and storage ID;
- realized SoC at all 65 boundaries, including initial boundary 0;
- controlling attempt ordinal and ID per interval;
- warm-start source/signature evidence (Section 5).

The comparison is deliberately observational. It reports raw numerical
differences without an automatic cross-run acceptance threshold or S3 decision.
Existing M17/P0 tolerances may be displayed as unit-compatible descriptive
context, but they are not repurposed into a new equality gate and are never
loosened after inspecting the comparison.

**Actual-start comparison semantics (Section 5 Tier B).** The `assigned_start`
and `solver_x0` payloads are heterogeneous - voltage, angle, generator,
storage, and auxiliary coordinates plus structural/layout data - so a single
SoC-recurrence tolerance is not automatically appropriate. The protocol freezes,
before any run:
- **exact equality** for all structure: `solver_x0_layout` /
  `layout_signature`, keys, array shapes, coordinate order, and
  `solver_evidence` coordinate counts;
- **nonfinite values rejected** anywhere in the start arrays;
- **numerical start arrays** compared **per named variable group**, rather than
  relying on one global number across unlike units;
- both **maximum absolute** and **normalized** (scaled) coordinate differences
  reported per group, with the normalization rule frozen before execution;
- residuals reported per boundary (0, 16, 32, 48), not only as a pass/fail.
Structure divergence is always a hard mismatch; numerical divergence is
reported for review rather than silently converted into a pass/fail label.

## 8. Partial-run and unexpected-termination handling

Distinct recorded classifications: `planned_recycle`, `study_complete`,
`rss_limit`, `total_wall_limit`, `checkpoint_stall_limit`, `worker_failure`,
`artifact_failure`, `outer_failure`, `recovery_exhausted`, `provenance_mismatch`.
A shorter trajectory is a valid, explicitly partial record. No non-planned
termination auto-continues; it stops the arm pending review. Raw solver
`infeasible`/`user_limit` is retained without promotion to a global
infeasibility certificate.

## 9. Raw and compact result schema

**Two phases, to keep execution provenance clean.** A clean worktree and
matching start/end context are required, so runtime writing of any *tracked*
file is forbidden - that is exactly the dirtiness that complicated S2:
1. **Execution phase:** workers and the supervisor write **only** ignored
   artifacts under `results/recycle_comparison/` - raw archives, resource
   samples, worker logs, supervision records, and ignored *provisional*
   summaries. No tracked file is touched while any worker runs.
2. **Post-execution analysis phase:** after every worker has exited and all
   provenance records are closed, `recycle_analysis.py` independently
   reconstructs results from the ignored artifacts and deliberately promotes
   the compact metadata to the tracked experiment-root
   `RECYCLE_COMPARISON_RESULTS.json`. The tracked result is post-hoc evidence,
   not an execution input, and is therefore **not** part of the pre-execution
   comparison implementation fingerprint (Section 1).

**Per worker (invocation) retained:** invocation number; global interval range
attempted/completed; PID (diagnostic); start/end monotonic + wall timestamps;
start/end execution context; launch and source commit; resume flag; planned
stop boundary vs observed completed boundary; termination classification and
reason; first/peak/final external RSS; late-worker RSS summary; restart
overhead; worker-log SHA-256; supervision-record SHA-256; the comparison
implementation fingerprint; and checkpoint SHA-256 before and after the
invocation. For the **first invocation** of an arm there is no prior checkpoint:
record the before-checkpoint hash as `null` and additionally record the verified
seeded outer-artifact SHA-256, so the invocation still binds a verified starting
identity.

**Archive continuity checks (Section 8 of review) retained per arm:** a completed
arm contains global intervals exactly 0..63 once each; a valid partial arm
contains exactly one contiguous prefix 0..`k-1`, with no gaps or duplicates.
For either outcome, no window archive is missing or duplicated; each checkpoint's
immutable window prefix extends the prior by exactly one interval;
resource-evidence chain is continuous through process boundaries; outer-plan
artifact identity is unchanged; storage IDs and order are unchanged; realized
SoC at a pre-restart checkpoint equals the resumed state; and the post-restart
causal source references interval `k-1` when resuming at `k`. These invoke
existing schema validators and retain their results.

**Compact tracked `RECYCLE_COMPARISON_RESULTS.json`:** arm directory names; SHA-
256 of each arm's final checkpoint, the shared outer plan, supervision records,
and analysis output; per-arm external first/peak/final RSS and the complete
global-interval-indexed safe-boundary RSS series; restart-to-first-checkpoint
times with matched `never` baselines; warm-start evidence per restart boundary;
numerical-agreement residuals; machine and
software provenance; both provenance fingerprints (Section 1). Large raw window
artifacts stay ignored (the verified checkpoint already binds the immutable
prefix; they are not re-hashed individually in the compact record).

**Tracking boundary.** The experiment-level `.gitignore` already ignores the
whole `results/` tree, and a nested `results/recycle_comparison/.gitignore`
**cannot** unignore itself or a compact file beneath an already-ignored parent
(confirmed with `git check-ignore`). Therefore:
- **all** raw and provisional comparison output stays under ignored
  `results/recycle_comparison/` (window archives, resource samples, worker
  logs, checkpoints, provisional summaries) - no nested-`.gitignore` exception
  is attempted;
- the single **tracked** compact result lives at the **experiment root** as
  `RECYCLE_COMPARISON_RESULTS.json`, exactly like the existing tracked
  `S2_RESULTS_METADATA.json` (verified un-ignored there);
- the tracked reference seed lives outside `results/` under `reference/`
  (`outer-plan.json.gz`, `s2_first64_reference.json`,
  `s2_first64_reference.sha256`, `extract_s2_reference.py`), tracked by default
  and never confused with ignored run output.

## 10. Focused pre-run tests (before the expensive Case118 run)

Inexpensive tests, using the small P0 fixture or mocks where full solves would
be excessive:
- `never` completes a tiny horizon in one worker;
- `recycle_2` over a tiny horizon stops/resumes at expected boundaries;
- no recycle at the final boundary;
- planned recycle stays distinct from rss/wall/stall termination;
- checkpoint continuity across multiple planned restarts;
- invocation numbers continue correctly across restarts;
- warm-start source after restart references the preceding accepted attempt;
- resume from an invalid/incomplete checkpoint is rejected by existing
  verification;
- first invocation uses checkpoint-free `resume=True` (no checkpoint present)
  and takes the `recovered_outer_without_checkpoint` path;
- later invocations verify the seeded outer artifact in place and never
  overwrite it;
- restart-overhead detection ignores the preexisting checkpoint and only
  accepts a new checkpoint at `restart_boundary + 1` with a changed hash;
- planned boundaries remain correct after a reviewed interruption resume
  (no immediate stop, no re-trigger of a passed boundary);
- analysis flags a deliberately altered action, SoC, **source record,
  `assigned_start`, or `solver_x0`** in a copied fixture (negative control -
  proving the comparator detects start/label divergence, not only trajectory
  divergence).

## 11. Final resource and wall-time limits

- **External RSS ceiling:** 24 GiB (per approval), so an elevated-band arm is
  observed rather than terminated. Record RSS and any readily available swap
  pressure; no system profiling.
- **No-checkpoint stall limit:** 60 minutes (S2 max single window ~21 min leaves
  margin). Retained from S2.
- **Per-arm wall ceiling:** 4 hours. Evidence: S2 first-64 in invocation 0 ran
  well under this even with the one ~1,253 s outlier window; restart overheads
  are few. Prevents an unattended stalled solve; not a success criterion.
- **Total comparison wall ceiling:** 12 hours across the three arms.
- **Cumulative wall accounting (explicit, so recycling cannot silently reset an
  allowance):**
  - per-arm time accumulates across **all** worker invocations of that arm,
    including planned recycles and any reviewed interruption resumes;
  - total time accumulates across all three arms;
  - restart overhead and process-launch time **count** toward the ceilings;
  - post-execution analysis time does **not** count toward the execution
    ceilings but is reported separately;
  - a rerun that discards and recreates an arm directory (Section 3) starts that
    arm's accounting fresh; time spent in the discarded, stale invocations is
    excluded from the ceiling but is still reported as discarded effort. Only
    invocations belonging to the retained arm directory count toward the
    ceiling.

## Import boundary (resolved)

Import public functions from `streaming_driver`, `streaming_schema`,
`streaming_archive`, and the fixture/policy modules. Write a small
comparison-owned supervisor. Do not refactor frozen S2 helpers into a shared
module and do not pull private `_underscored` helpers out of `run_s2.py`.
Preserving S2 unchanged outranks forcing a new abstraction in this observational
study.

## New files (all under `experiments/case118_annual_hierarchy/`)

- `run_recycle_comparison.py` - comparison-owned supervisor + worker + observer;
  all invocations `resume=True` (checkpoint-free first, checkpointed later).
- `RECYCLE_COMPARISON_PROTOCOL.md` - freezes Sections 1-11 with concrete hashes
  and numerical reporting definitions; states observational scope, no-S2-change, and
  the two provenance identities.
- `recycle_analysis.py` - independent reconstruction: RSS summaries, restart
  overhead, archive-continuity via existing validators, warm-start evidence,
  and numerical agreement vs `never` and the tracked S2 first-64 reference.
- `reference/outer-plan.json.gz` - tracked byte-identical S2 outer artifact.
- `reference/s2_first64_reference.json` - tracked compact deterministic first-64
  extract (binds the S2 final-checkpoint hash; includes boundary 0 and the
  actual-start fields).
- `reference/s2_first64_reference.sha256` - tracked sidecar recording the
  extract's SHA-256 (the extract cannot contain the hash of its own bytes).
- `reference/extract_s2_reference.py` - validated-extraction regenerator:
  verifies the S2 source via existing validators, extracts, serializes
  canonically, compares generated bytes to the tracked JSON, and verifies the
  sidecar hash.
- `tests/` additions for Section 10.
- `RECYCLE_COMPARISON_RESULTS.json` - the single **tracked** compact result at
  the experiment root (like `S2_RESULTS_METADATA.json`), promoted post-run.
- `results/recycle_comparison/` - **entirely ignored** raw and provisional
  output (covered by the existing `results/` ignore rule; no nested
  `.gitignore` exception is used or needed).

No edits to `run_s2.py`, `streaming_*.py`, or any S2 result artifact.

## Explicitly out of scope

Root-causing the elevated resumed-worker band; any change to frozen S2; choosing
a production recycling interval (decided after we see the numbers together).

## Resolved decisions (previously open)

1. **Cross-run numerical interpretation:** the comparison reports raw absolute
   and normalized residuals without introducing an automatic equality gate.
   Existing P0/M17 tolerances may appear only as unit-compatible descriptive
   context and are not adjusted after observing the results.
2. **Wall ceilings:** 4 h per arm, 12 h total (Section 11), approved.
3. **Negative-control test:** included (Section 10, last bullet). The comparator
   must detect a deliberately altered action, SoC, or source record in a copied
   fixture, proving it can report divergence rather than always reporting
   agreement.
