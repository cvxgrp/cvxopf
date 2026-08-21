# P0 streaming-runner equivalence protocol

P0 freezes the experiment-owned streaming runner and its equivalence evidence
before any week-scale case118 execution. The runner is an experiment tool, not
a public controller and not a replacement for `HierarchicalResult`.

## Equivalence fixtures

Exact orchestration equivalence uses a compact deterministic case9 fixture at
6 and 24 hourly intervals. This is deliberate: S0/S1 established that one
six-hour case118 AC endpoint already takes 19–35 minutes and roughly
10–15 GiB. Executing a 24-window case118 public hierarchy merely to test
network-independent orchestration would violate the experiment's resource
gate.

The compact fixture uses:

- repository `case9()` with its hash frozen by the fixture manifest;
- explicit first-class generators and loads with deterministic IDs;
- one ideal storage device at bus 7 with explicit ID `p0_storage_bus_7`;
- fixed nonsheddable loads and no HVDC or nondispatchable generation;
- deterministic hourly demand multipliers stored as a compact formula and
  checked prepared-array hash;
- 50% initial and global terminal SoC;
- `outer_policy="frozen"`;
- `inner_terminal_policy="hard_equality"`;
- `initialization_policy="shifted_with_recovery"`;
- three-hour AC windows, including final truncation; and
- the exact M17 solver configurations, perturbation scales, seeds, status
  classifier, and residual tolerances.

The complete policy is bound to SHA-256
`2186334bd2e7be3760636f0b20575c81deaff5f293fb9a725270157379957520`.
The two-layer solve configuration is bound separately to SHA-256
`bfb818de03ddbfd983bb02def3aa3c51d0e6c1b075486ec66bca3035d82e2977`:
CLARABEL for the outer solve and IPOPT for AC, both with the empty explicit
option mapping used by the M17 reference run. Any policy tolerance, recovery
scale or seed, solver, or solver-option change is a different protocol and is
rejected before model construction.

The 6-hour and 24-hour nominal cases compare the complete public result with
the streaming archive window by window. The comparison includes outer
signposts, slot registry, starts, complete reduced IPOPT `x0`, statuses,
residuals, controlling attempts, executed actions, realized SoC, termination,
and every exact-once summary.

Case118 applicability is checked separately without a 24-window public run:

1. reconstruct the accepted S1 24-hour outer and selected six-hour endpoint
   through the streaming build seams;
2. compare dimensions, identities, SoC handoff, structural signatures,
   extracted results, audits, and compact archive fields with the immutable
   S1 artifact; and
3. release the live AC build and verify that the archive remains sufficient to
   reproduce every acceptance and accounting decision.

This is a network/model boundary gate, not full case118 trajectory
equivalence. Passing both the compact orchestration matrix and the case118
archive gate is required before S2.

## Frozen nine-slot registry

Every shifted-recovery window registers these slots before execution:

| Ordinal | Role | Source |
|---:|---|---|
| 0 | primary controlling | generated flat at iteration 0; immediately preceding accepted controller later |
| 1 | target-free | same causal start as slot 0 |
| 2 | copied target-free | accepted current-window slot 1 |
| 3–5 | perturbed target-free | accepted current-window slot 1, scales `1e-4`, `1e-3`, `1e-2` |
| 6–8 | perturbed causal | slot-0 causal center, scales `1e-4`, `1e-3`, `1e-2` |

Seeds use `17_000_000 + 100*iteration + 10*source_code + scale_index`,
where target-free has source code 1, causal has source code 2, and scale index
is 1–3. Arrays use sorted variable names and Fortran-order scalar traversal.

## Deterministic injected-outcome matrix

Injection occurs only in the equivalence harness. Production streaming code
does not accept fault schedules and does not import private M17 orchestration
helpers. The harness instruments the private public-controller solve seam so
both orchestrators observe the same declared outcomes.

| Case | Iteration | Frozen outcomes | Expected controller |
|---|---:|---|---|
| nominal shifted primary | all | slot 0 accepted | slot 0; slots 1–8 not needed |
| copied recovery | 0 | slot 0 solver failure; slot 1 accepted; slot 2 accepted | slot 2 |
| target-free perturbation | 0 | slot 0 solver failure; slot 1 accepted; slots 2–3 solver failure; slot 4 accepted | slot 4 |
| causal perturbation | 1 after accepted iteration 0 | slot 0 solver failure; slot 1 solver failure; slots 2–5 source unavailable; slot 6 accepted | slot 6, sourced from iteration 0 controller |
| certified infeasibility | 0 | slots 0, 1, and 6–8 certified infeasible; target-free-derived slots unavailable | none; recovery exhausted with certified-infeasible diagnosis |
| exception then unusable | 0 | slot 0 solver exception; slot 1 unusable primal; slots 2–5 unavailable; slot 6 accepted | slot 6; distinct retained outcomes |
| recovery exhaustion | 0 | slots 0, 1, and 6–8 solver failure; target-free-derived slots unavailable | none; no state advance |

Every undeclared later slot after an accepted controller is
`not_needed_after_acceptance`. Every source-dependent slot whose declared
source is unavailable is `source_unavailable`. Attempt IDs, source IDs,
ordinals, transformations, and termination strings must match exactly.
Attempt IDs retain the public M17 form
`ac-{iteration:03d}-{ordinal:02d}-{role}` without translation. As in the
public records, `not_needed_after_acceptance` and `source_unavailable` slots
carry neither a source kind nor a source attempt ID; executed attempts and
construction failures retain the causal source actually used or reached.

The causal-perturbation case cannot run at iteration 0. Its iteration-1 slots
must reference the accepted iteration-0 controlling attempt and no older or
retrospective source.

## Archive transaction

For each controller iteration, the streaming runner:

1. validates the resume state and registers every slot;
2. constructs and solves attempts in policy order;
3. retains full live evidence until the window decision is complete;
4. derives the executed first action only from the first accepted controlling
   attempt;
5. writes one immutable window archive atomically and verifies its hash;
6. writes the realized-state/checkpoint index atomically;
7. releases every AC `OPFBuild`; and
8. only then proceeds to the next iteration.

The archive stores no live build. It retains formulation, dimensions,
ordered variable/constraint/parameter structural signatures, named assigned
starts, complete reduced `x0` and coordinate layout, extracted public results,
audit and solver evidence, source provenance, executed action, post-step SoC,
and artifact hash. Executed slots are invalid unless their assigned model
coordinates reproduce the original-variable coordinates in `x0`, their layout
accounts for every model and auxiliary coordinate, the layout signature
matches, and problem-object identities are unchanged. Serialized solve audits
must also satisfy the public M17 status/outcome classifier: accepted records
use only the fixed eligible statuses and retain no missing fields, identity
error, or exception; infeasible and solver-failure records retain their
distinct required evidence and all solver-stat fields. The loader supplies the
frozen residual tolerances externally and requires every common AC residual,
plus `terminal_soc_mwh_abs` on controlling attempts. The expected policy and
every attempt must be `hard_equality`; soft-terminal behavior remains outside
this frozen experiment contract. Target-free attempts require only the common
AC set. Eligible solves are accepted if and only if all applicable gates pass.
The loader also receives the frozen trajectory horizon, three-hour AC-window
length, and fleet/network dimensions. Every archive must satisfy
`interval_stop = min(iteration + 3, horizon)`. Executed results retain the
complete conditional AC result schema, with finite arrays shaped by the
window length and declared generator, bus, branch, load, storage,
nondispatchable, and HVDC counts.
The trusted configuration also supplies `delta_hours=1.0` and the complete
ID-aligned outer SoC boundary trajectory. Each archive must use the exact
hourly duration, and its terminal target must match the frozen outer boundary
at that archive's `interval_stop` within the frozen SoC tolerance. Neither
quantity is trusted merely because the archive is internally self-consistent.

An observer may request termination only after steps 5–7. Resume validates the
source/scenario/outer-plan hashes, a frozen-policy hash, the exact frozen SoC
tolerance, ordered archive index, last realized SoC, completed-interval count,
and exact next iteration. Archive-declared tolerances are never trusted as
validation policy. Missing, duplicate, altered, or future records reject
resume; they are never silently blessed. Each later window must name the
actual controlling attempt from the immediately preceding verified archive;
the checkpoint does not trust a merely well-formed preceding-attempt ID.

## Complete trajectory driver contract

`streaming_driver.run_streaming_trajectory()` owns the full fresh/resumed loop
without returning a public `HierarchicalResult`. A fresh run snapshots its
inputs, solves and archives the outer plan once, then replaces the live outer
build with its verified build-free signposts before constructing any AC
window. A resumed run verifies that same outer artifact and the complete
checkpoint/window chain before reconstructing the immediately preceding
controller from archived named solution arrays.

Every executed AC attempt reports process-memory observations immediately
before and after model construction and solution. The driver also observes
immediately before and after window archival and after live-build release.
These internal observations never interrupt a solve or persistence transaction. A caller's
resource observer receives state only at the final safe boundary, after the
window artifact and checkpoint are durable and garbage collection has released
the live build. Its optional reason produces an explicit
`observer_terminated` record without advancing another interval.

RSS is current resident memory, not the process-lifetime high-water mark. On
macOS it is read in-process through `proc_pidinfo`; on Linux it is read from
`/proc/self/statm`. Each invocation-labeled sample is written exactly once in
an immutable, backward-linked resource chunk. The newest chunk is published
first, and only then does one atomic checkpoint replacement make the window,
state, and resource-chain head jointly resumable. The prior checkpoint and
its chain remain valid until that replacement. Resume verifies the complete
chain before adding the next invocation's samples and rejects missing,
altered, cyclic, provenance-mismatched, count-mismatched, or
interval-discontinuous evidence.

Immediately after an accepted outer plan is archived and its live build is
released, the driver publishes the first resource chunk and a zero-interval
checkpoint. A crash before the first AC window therefore resumes from the
already-audited outer plan instead of forcing an overwrite or re-solve. If
publication is interrupted after the immutable outer plan—or after an orphaned
initial resource chunk—but before the checkpoint appears, resume verifies the
outer artifact against the current input, policy, solver, signpost, and storage
contracts. It also reconstructs the complete lossy-DC audit from the archived
finite public results and requires exact agreement among the accepted status,
residuals, storage identities, result SoC trajectory, initial state, and hashed
signposts. Only then does it republish a new zero-boundary resource head and
begin AC work.

Fresh mode rejects an existing outer plan or checkpoint rather than blessing
or replacing it. Normal resume requires both; checkpoint-free zero-boundary
recovery requires the immutable outer artifact, whose original source and
scenario hashes must match the caller alongside its input, policy, solver,
signpost, and storage contracts. Recovery exhaustion archives the full failed
nine-slot window without advancing the checkpoint. Artifact failure,
outer failure, observer termination, and successful horizon completion receive
distinct atomic run-status records.

## S3b retrospective gate

P0 also normalizes the authoritative M17 S3b recovery window and compares its
copied-target-free chain with the public S7 interpretation and streaming
archive schema. This is retrospective evidence for a naturally observed
recovery path. The deterministic injected matrix remains the proof for all
other paths.

## Advancement gate

P0 passes only when:

- nominal 6- and 24-hour compact runs are exactly equivalent;
- every injected recovery and termination case matches its complete tree;
- the authoritative S3b copied-recovery evidence normalizes without loss;
- the case118 S1 outer/endpoint archive gate passes;
- resume, corruption rejection, atomicity, resource-observer termination, and
  post-release reconstruction tests pass; and
- no production streaming module imports `cvxopf._hierarchical_solver` or any
  M17 experiment runner.
