# M14a legacy baseline protocol

M14a characterizes the existing per-step temporal graph before vectorized
assembly changes it. The legacy representation is explicitly `stepwise`; its
convex canonicalization baseline is CPP. AC uses the existing DNLP/IPOPT path.

Each scaling point runs in a fresh process so process-lifetime peak RSS is
meaningful and cannot inherit an earlier point's high-water mark. A record
retains construction, explicit convex canonicalization, solve, and extraction
times; phase-boundary peak RSS; source and canonical graph dimensions; result
schema and values; independently reconstructed physical residuals; status; and
strict-JSON result size. For AC, DNLP
canonicalization remains included in solve time because CVXPY exposes it
through the nonlinear solve path rather than `Problem.get_problem_data()`.

The deterministic feasible fixture uses fixed Case9 loads and one hard-target
storage unit whose initial and terminal SoC are both 50 MWh. The failure
fixture changes the same device to an unreachable 0-to-100 MWh transition,
producing genuine solver-certified infeasibility rather than a synthetic status
rewrite. Case118 scaling uses the same declared storage fixture and native case
loads; it is characterization only and is not the Case118 annual experiment.
The full component fixture additionally exercises one sheddable load,
time-varying nondispatchable availability, and an HVDC transfer alongside
storage. Singlenode DC deliberately retains the applicable components while
omitting its unsupported HVDC path.

Machine-dependent times and RSS values are observations, not portable golden
tests. Source/result schemas and graph dimensions are frozen separately in the
focused M14 tests. Every worker records its commit, source fingerprint,
worktree state, platform, architecture, and solver-stack versions. The parent
manifest validates every worker against the parent's commit and source
fingerprint, then hashes every retained result, phase journal, and log artifact.
Scaling stops after the first
unsuccessful or timed-out worker for each formulation and preserves that
classification in the immutable manifest. The default per-point wall limit is
1,800 seconds and authoritative runs must record any reviewed override.

The frozen legacy ladders are formulation-specific:

- Case9 AC: 1, 2, 4, 8, and 24 steps.
- Case9 lossy DC and single-node DC: 1, 2, 4, 8, 24, and 168 steps.
- Case118 AC: 1 and 3 steps.
- Case118 lossy DC: 24, 168, and 720 steps.
- Case118 single-node DC: 24, 168, 720, and 8,760 steps.

The bounded AC ladder is characterization, not an annual-AC feasibility
claim. The Case118 lossy-DC ladder stops at 720 because the already retained
failed annual S4 attempts supply the 8,760-step legacy resource-boundary
evidence; M14a does not repeat that expensive failure. The single-node path
retains 8,760 because its smaller per-step network graph is a useful annual
legacy comparator.

Independent analysis re-verifies every manifest artifact identity, execution
fingerprint, point ordering, assembly/backend choice, accepted status, result
schema, and physical residual gate. Workers retain the minimal numerical audit
inputs, allowing analysis to reconstruct the residuals without rebuilding the
large legacy CVXPY graph. Each ladder is a separately retained
record; neither alone authorizes advancement. The consolidated M14a result
requires both complete ladders from the same commit and source fingerprint.
The two executions must also share platform, architecture, Python, package,
and underlying IPOPT-library versions so their timing and memory observations
form one comparable baseline. The compact result retains readable source and
canonical structures, objectives and component-cost scalars, artifact sizes,
and their corresponding digests; ignored raw artifacts are not required to
interpret the baseline.
Authoritative advancement and promotion normally require clean parent and
worker provenance. A reviewed exception may qualify a dirty worker only when
the parent launched clean, the execution commit and M14 source fingerprint
remain unchanged, and an explicit retained record names the non-execution
paths and scientific reason. The dirty worker remains visible in the compact
result. Post-execution analysis may use a later clean committed analyzer when
its own commit and source fingerprint are retained separately; correcting an
analyzer never rewrites execution provenance. Worker return codes,
classifications, and artifact availability must also match the frozen
supervisor outcome matrix.
An unsuccessful point and the intentionally omitted later points remain valid
partial characterization evidence but cannot be labeled a complete M14a
baseline or authorize M14b advancement.

From a clean committed tree, the authoritative execution and consolidation
commands are:

```bash
uv run python -m experiments.m14_time_vectorization.run_m14a \
  --frozen-ladder case9 \
  --output experiments/m14_time_vectorization/results/m14a-case9
uv run python -m experiments.m14_time_vectorization.run_m14a \
  --frozen-ladder case118 \
  --output experiments/m14_time_vectorization/results/m14a-case118
uv run python -m experiments.m14_time_vectorization.m14a_analysis \
  experiments/m14_time_vectorization/results/m14a-case9 \
  experiments/m14_time_vectorization/results/m14a-case118 \
  --reviewed-worktree-exception \
  experiments/m14_time_vectorization/M14A_EXECUTION_REVIEW.json \
  --promote experiments/m14_time_vectorization/M14A_RESULTS.json
```

Raw worker artifacts remain ignored under `results/`; the independently
reconstructed compact result is the tracked scientific record.
