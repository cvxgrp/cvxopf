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
