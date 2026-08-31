# M14c prefix representation profiling protocol

## Purpose and status

This checkpoint supplies the representation/backend attribution required by
M14's profiling matrix before annual S4 execution is authorized. It is a
non-promotional comparison: it neither requalifies nor replaces the accepted
vectorized prefix ladder, and it cannot authorize annual execution by itself.

The comparison uses exactly the frozen Case118 S4 prefixes at `T = 24`, `168`,
and `720`. The completed vectorized evidence is read from
`results/m14c_case118_prefix_ladder`; it is never modified. Each comparison
point runs the same prefix in a new supervised process with:

- `temporal_assembly="stepwise"`;
- CVXPY's `CPP` canonicalization backend;
- the unchanged S4 network, components, profiles, identities, timestep,
  terminal policy, objective, CLARABEL configuration, and audit tolerances;
  and
- the same horizon-specific RSS and wall-time limits used by the accepted
  vectorized ladder.

No cross-products such as stepwise plus SCIPY or vectorized plus CPP are part
of this checkpoint. They may be characterized separately, but they are not
needed to attribute the two production paths actually retained by M14.

The reference execution commit is
`2f0f95288e5e50ff32c94c7667ea44121f719fa4`. Its immutable
`ladder-result.json` SHA-256 is
`1c66941363d59cc29374eee2201eb2e2cf0a5393b4bfb76edfe3b99aef7cbca6`.
The profiling runner must verify that identity and the complete referenced
artifact chain before starting its first worker.

## Frozen execution sequence

The stepwise points execute in the order `24`, `168`, `720`, with one fresh
worker and one immutable point directory per horizon. Source, fixture, policy,
solver, representation, backend, machine/software context, resource samples,
worker log, result archive, and supervision outcome are retained. The runner
stops after the first nonaccepted point and never retries automatically.

Catchable interruption terminates and joins the active worker and preserves a
nonpromotable partial lifecycle. Resource, construction, solver, audit,
provenance, and process-control failures retain their actual classifications.
The vectorized reference tree and M14c annual-authority record remain
read-only throughout execution and analysis.

## Retained measurements

For every attempted stepwise point, retain:

- model-construction wall time;
- combined CVXPY canonicalization and CLARABEL solve wall time;
- result-extraction wall time;
- independent-audit wall time;
- archive-publication and release wall time;
- total worker and supervisor wall time;
- first, peak, and final externally sampled RSS; and
- worker-local phase RSS samples.

The accepted vectorized artifacts predate the separate extraction/audit phase
markers. Their retained `after_solve` to `after_archive` interval is therefore
reported, without reinterpretation, as the combined post-solve
extraction/audit/publication tail. Construction, canonicalization/solve, total
worker wall time, and peak RSS are directly comparable across both paths.
This known timing-resolution asymmetry is recorded in the result and is not a
reason to repeat the accepted vectorized ladder.

The analyzer reports, for each horizon and each production path:

- the measurements above at their actual retained resolution;
- objective and declared component costs, including independently reconstructed
  lossy-DC network-loss cost and the resulting objective-accounting residual;
- accepted-primal status and the complete independent residual audit;
- storage identities, boundary indices, and terminal SoC signposts; and
- canonical/source structural counts as representation-specific
  characterization, not equality requirements.

It also reports stepwise/vectorized ratios and absolute differences only where
the underlying timing boundaries match. Performance is descriptive: a small,
zero, or negative speedup does not invalidate an already accepted vectorized
ladder.

Timing comparability is classified explicitly from a shared production/model
fingerprint over `src/cvxopf` and the frozen S4 fixture/model/audit files, plus
platform, architecture, and software versions. Harness-specific commits and
fingerprints remain visible provenance but do not by themselves make the
production comparison unmatched. Ratios remain descriptive when the shared
contexts differ; the analyzer must list every mismatched field rather than
imply a controlled same-binary or same-environment comparison.

## Scientific gate

Each accepted stepwise point must independently satisfy the frozen M14c/S4
audit and must agree with its vectorized reference within the existing M14c
mathematical tolerances for objective, component costs, storage trajectories,
terminal signposts, and other uniquely determined quantities. Alternative
feasible optimal values remain allowed only for genuinely nonunique
coordinates when both arms pass the complete physical and optimality gates.
In particular, lossy-DC `p_flows` are not coordinate-equality-gated because
zero-resistance cycles can make branch flows nonunique. Their schema and shape
must match, both arms must independently pass the complete branch/nodal audit,
and objective and component-cost equivalence remain mandatory.

A mathematical or audit mismatch blocks annual authorization. A performance
result never does. A retained resource or process boundary is descriptive
evidence and requires review; it is not relabeled as a mathematical failure.

The analyzer writes its reviewed comparison only inside the ignored profiling
directory. There is no tracked promotion command. Annual execution remains
blocked until the accepted vectorized ladder and this profiling record have
both been reviewed and the separate integration authority is explicitly
updated.
