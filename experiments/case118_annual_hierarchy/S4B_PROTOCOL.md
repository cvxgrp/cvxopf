# Case118 S4b shard qualification protocol

## Status and scope

**Open; shard-boundary rule frozen, manifest and execution implementation
pending.** S4b qualifies scheduler-neutral partitioning of the accepted annual
Case118 hierarchical AC realization. It does not alter the accepted S4 outer
trajectory, tune boundaries against AC outcomes, add scheduler behavior to
`cvxopf`, or execute the annual AC study.

The only authoritative outer source is the accepted 8,760-hour
`attempt_005` archive:

- compact S4 result SHA-256:
  `f8194ef39d18084f90d0d6216bd1a7ee85a889bf3699571fd9f7f2b3c3dc4947`;
- outer archive SHA-256:
  `6e7d88e8eed39de4a0141b0fe3c8a146fd2ae298a3d3ddc2768ae57247e87031`;
- outer signpost SHA-256:
  `afc1bc32d4ea453e9ee0bf32c99003cd4952e84746f72ca1e574721a40e15e5a`;
- ordered storage identities: `storage_bus_41`, `storage_bus_65`,
  `storage_bus_89`, and `storage_bus_105`.

The signpost digest above must be independently verified from the archive
before manifest publication. A mismatch blocks S4b; it is never repaired by
regenerating an expected hash.

## Frozen annual boundary rule

Let `H = 8760`, let `e[t, i]` be the retained outer boundary SoC at global
boundary `t`, let `b[t, i]` be outer storage power during interval `t`
(positive means discharging), and let `E[i]` and `P[i]` be storage energy and
power ratings. Boundary indices are global half-open interval boundaries.

The annual partition uses a nominal shard length of 730 hours, a minimum
ordinary length of 672 hours, and a maximum ordinary length of 792 hours.
Starting from `p = 0`, while `H - p > 792`, enumerate integer candidates

```text
t in [p + 672, min(p + 792, H - 1)].
```

For each candidate:

1. Device `i` participates locally when
   `max(abs(b[k, i])) >= max(1e-6 MW, 0.001 * P[i])` over
   `k in [max(0, t - 3), min(H, t + 3))`. This six-hour neighborhood prevents
   a single zero-power interval from hiding a device that is moving through
   the boundary. Devices below the threshold throughout the neighborhood are
   explicitly stationary for scoring and are omitted from the midpoint
   maximum; their identity-aligned SoC remains in the manifest.
2. The normalized charging statistic is
   `c(t) = sum_i max(-b[t - 1, i] / P[i], 0)`.
3. A candidate is eligible only when at least one device participates and
   `c(t) >= 0.001`.
4. The device-level midpoint deviation is
   `d(t) = max_i(abs(e[t, i] / E[i] - 0.5))` over locally participating
   devices. This worst-device score prevents aggregation from concealing a
   participating device near an energy bound.

Choose the eligible candidate by the following exact lexicographic order:

1. smallest `d(t)`;
2. largest `c(t)`;
3. smallest `abs(t - (p + 730))`; and
4. earliest global boundary.

The selected boundary becomes the next `p`. If a candidate range contains no
eligible boundary, manifest construction fails for review; there is no hidden
fallback. When the remaining horizon is at most 792 hours, append `H`. The
final shard is an explicitly allowed truncation and may be shorter than 672
hours.

Applied to the accepted S4 archive, the rule produces these boundary indices:

```text
0, 682, 1452, 2213, 2965, 3723, 4468,
5211, 5956, 6726, 7475, 8187, 8760
```

The corresponding shard lengths are:

```text
682, 770, 761, 752, 758, 745,
743, 745, 770, 749, 712, 573
```

These indices are a deterministic consequence of the frozen rule and outer
archive. The immutable manifest must retain every candidate score needed to
rederive each selection, all four boundary SoCs, charging/participation
metadata, source hashes, and the rule hash.

### Canonical rule and manifest identities

The boundary rule is also represented as one machine-readable `rule_payload`
containing its schema version, horizon, nominal/minimum/maximum lengths,
participation neighborhood and threshold, charging threshold, score and tie-
break order, final-truncation rule, authoritative S4/outer/signpost hashes, and
ordered storage identities. Its identity is
`SHA256(canonical_json(rule_payload))`.

For S4b, `canonical_json(x)` is the UTF-8 encoding of Python
`json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
allow_nan=False)` with no trailing newline. Payloads contain only JSON objects,
arrays, strings, booleans, integers, finite binary64 values serialized by that
encoder, and `null`. The implementation must freeze characterization tests for
every float-bearing object rather than depend on a different language's
default JSON formatting.

The annual artifact uses a noncircular envelope:

```text
{
  "schema_version": 1,
  "manifest_sha256": SHA256(canonical_json(manifest_payload)),
  "manifest": manifest_payload
}
```

`manifest_payload` contains `rule_payload`, `rule_sha256`, global provenance,
and the ordered shard entries. It does not contain `manifest_sha256`, and shard
entries do not contain it. Every worker receives the complete envelope plus a
shard ID, verifies the envelope digest and rule digest, and only then selects
its entry. This makes the same immutable artifact usable by workers and the
independent analyzer without self-reference or an external hidden identity.

## Manifest contract

Each shard entry must retain:

- a stable shard ID and global half-open `[start, stop)` interval;
- exact input, scenario, network, policy, solver, representation, source, S4
  result, outer archive, signpost, and boundary-rule hashes; the enclosing
  envelope, not an entry, carries the manifest hash;
- ordered storage identities plus initial and terminal SoCs copied verbatim
  from outer boundaries `start` and `stop`;
- three-hour AC window, one-hour stride, hard-equality terminal policy, frozen
  nine-slot recovery configuration, and typed 300-second primary budget;
- deterministic perturbation seeds in global—not shard-local—coordinates;
- independent output/checkpoint locations and resource limits; and
- the expected predecessor/successor boundary identity.

The predecessor terminal state and successor initial state must be byte-
identical manifest values. A worker may neither infer a missing state nor
repair a mismatch. Shard order, worker order, and completion order are separate
concepts; only global interval order determines the merged trajectory.

## Five-minute primary safeguard

Before any numerical S4b or S5 AC execution, implement the experiment-owned
policy in `FIVE_MINUTE_TIMEOUT_POLICY.md`. Every target-constrained primary
attempt receives the typed `300.0` second solver wall budget. Timeout must stop
and join the attempt process, retain its complete evidence as `timeout`, and
enter the unchanged target-free then copied-target-free causal recovery path.
No timed-out primal is eligible for acceptance or state advancement.

The 300-second solver clock starts immediately before the call that performs
CVXPY canonicalization, complete reduced-coordinate/`x0` preparation and
verification, and IPOPT execution. It stops when that call returns or the
attempt process is terminated. Model/window construction and named-start
assignment occur before this clock and are reported separately. Attempt-
process startup, termination/join, archive publication, and worker restart are
orchestration overhead and are also separate. These boundaries match the
replay evidence used to adopt the policy.

Resource supervision follows process trees. At every sample, a worker's RSS
and CPU scope is the worker PID plus all live active-attempt descendants,
identified by `(pid, create_time)` and counted once. The two-worker aggregate
is the union of both process trees, again deduplicated by `(pid, create_time)`;
the supervisor's own resources are reported separately and are not silently
charged to either worker. Peak RSS uses simultaneous current-RSS samples, not
the sum of per-process lifetime peaks. CPU uses the sum of user and system CPU
seconds over the same deduplicated tree. A missing or failed descendant sample
invalidates the corresponding resource gate rather than being treated as
zero.

Tests must inject the clock/process boundary without waiting five minutes and
prove that attempt identity, causal source, hard target, acceptance gates,
first-accepted stopping, and exact-once state advancement remain unchanged.
Timing output separates primary budget, target-free solve, copied solve,
construction/canonicalization where available, orchestration/restart overhead,
and total window latency.

## Qualification sequence

S4b advances only after the following ordered gates pass:

1. **Manifest derivation.** Independently rederive the boundary list and every
   identity-aligned state from the accepted S4 archive, then publish one
   immutable annual manifest. No AC result is inspected.
2. **Boundary-effect characterization.** Run the ordinary uninterrupted
   24-interval controller and a one-process execution with a declared boundary
   at interval 12. The partitioned arm truncates its final pre-boundary AC
   windows and therefore may differ scientifically from the ordinary three-
   hour controller. Retain and report attempt, action, state, residual,
   accounting, and runtime differences; do not impose coordinate equality or
   call this process equivalence. Both arms must independently pass the frozen
   acceptance gates.
3. **Process equivalence for the partitioned policy.** Compare the one-process
   partitioned arm with two sequential fresh-process shards using the same
   forced boundary at interval 12. Attempts, accepted actions, boundary states,
   residuals, accounting, and merged trajectory quantities must agree within
   existing frozen tolerances. This proves that process separation reproduces
   an already-partitioned policy, not that adding a boundary leaves the
   uninterrupted controller unchanged.
4. **Independent fresh-process execution.** Each qualification shard starts
   using only its manifest entry, installed source, and immutable inputs. It
   may not read predecessor in-memory state or an undeclared local artifact.
5. **Deterministic merge.** The merger rejects missing, duplicate,
   overlapping, out-of-range, differently configured, or boundary-
   discontinuous shards. Permuting valid shard completion order produces the
   same canonical merged digest and global summaries.
6. **Two-worker parallel demonstration.** Run the same two bounded shards
   concurrently in fresh processes under a predeclared 24 GiB aggregate RSS
   ceiling and 16 GiB per-worker ceiling. Retain per-worker and aggregate peak
   RSS, CPU time, solve time, orchestration time, elapsed critical-path time,
   recovery behavior, and requested versus achieved concurrency. Any resource
   crossing stops automatic execution and retains a partial record; it never
   changes cadence, boundaries, or solver policy.

Passing the bounded demonstration authorizes use of the immutable annual
manifest in S5. It does not claim universal parallel efficiency, authorize
automatic retry, or close M14d. M14d remains a separate single-node-DC and AC
vectorization track and does not block Case118 S4b/S5.

## Stopping point

This planning checkpoint freezes the scientific boundary rule and
qualification contract only. Manifest publication, timeout implementation,
worker/merger implementation, and numerical qualification require separate
reviewed checkpoints.
