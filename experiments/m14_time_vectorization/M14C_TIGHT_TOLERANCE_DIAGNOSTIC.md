# M14c tight-tolerance representation diagnostic

## Purpose and status

This post-hoc diagnostic investigates the small objective and trajectory
differences observed by the completed conditioned M14c profile, whose frozen
exact-coordinate gate classified the comparison as a mismatch. It is separate from the
authoritative vectorized ladder and from the descriptive profiling record. It
cannot promote a result, alter `M14C_INTEGRATION.json`, or authorize annual
execution.

The diagnostic repeats the frozen Case118 S4 prefixes at `T = 24`, `168`, and
`720` for both production representations:

- `stepwise` with the `CPP` canonicalization backend; and
- `vectorized` with the `SCIPY` canonicalization backend.

Every arm uses a fresh process. Network, components, profiles, identities,
timestep, terminal policy, objective, and audit tolerances remain unchanged.
The only solver change is the predeclared CLARABEL precision:

```text
tol_gap_abs = 1e-10
tol_gap_rel = 1e-10
tol_feas    = 1e-10
```

The runner binds the completed profiling root and analysis artifacts by
SHA-256 before creating output. Source commit, source fingerprint,
production-model fingerprint, platform, architecture, Python/package versions,
fixture hashes, and the exact solver options are retained.

## Retained evidence

Each arm retains:

- the complete public result schema and numerical result;
- the frozen independent M14c/S4 acceptance audit;
- independently reconstructed generator, branch-flow, storage-power,
  storage-energy, load-shedding, and nondispatchable bounds;
- storage recurrence, terminal equality, load-service, and renewable
  availability identities;
- objective decomposition into generation, storage/other public component
  costs, and lossy-DC network-loss cost, with an accounting residual;
- CVXPY solver name, status, iteration count, setup/solve time, and available
  extra statistics; and
- the CLARABEL primal objective, dual objective, absolute and relative gap,
  primal residual, dual residual, iteration count, solve time, and termination
  status captured directly from the solver result before CVXPY discards those
  fields.

The root comparison reports objective and component-cost differences, each
native solver gap, their combined absolute certificate scale, whether the
objective separation is covered by that scale, maximum coordinate differences
for `Pg`, storage power, storage SoC, and net injection, and the complete
bound/audit maxima. Branch-flow coordinates
remain residual-gated because zero-resistance cycles make them genuinely
nonunique.

## Interpretation

This is a diagnostic, not a retrospective tolerance waiver. If tighter solves
collapse the objective/loss gap while device trajectories remain different,
the evidence supports treating those trajectories as nonunique or weakly
identified in a separately reviewed protocol update. If the objective/loss gap
persists materially beyond the retained solver gaps, the explicit-bound and
leaf-bound formulations require further investigation.

The output directory is
`results/m14c_case118_tight_tolerance_conditioned`. It is ignored and immutable
per execution. A completed diagnostic still leaves annual execution blocked
until an explicit scientific review and separate authority update.
