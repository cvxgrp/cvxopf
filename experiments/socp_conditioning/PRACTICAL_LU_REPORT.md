# Practical SuperLU convergence results

Both declared conditions return native CLARABEL **Solved** and pass the
unchanged independent physical and accounting checks at a prospective relative
gap tolerance of 1e-9. The surplus primal, dual and slack vectors are exactly
the prior rejected SuperLU vectors: the new stopping criterion accepts the
accurate iterate before the next cone-scaling operation fails. Compensated
cone arithmetic is disabled and is not needed for these two accepted results.

This establishes a working experimental configuration on two conditions,
not a universal solver remedy, stock-wheel capability or production default.

## Declared check

Executed 2026-10-06 under [PRACTICAL_LU_PROTOCOL.md](PRACTICAL_LU_PROTOCOL.md).
Two serial fresh single-thread workers use the frozen normalized device cones,
exact fixed Pg/p_nd substitution and joint scaling, with the experimental
SuperLU KKT path. The surplus window is [3308,3332) with fixed loads; the
deficit window is [1165,1189) with its original optional shedding policy.
Removing shedding is a model choice, not a numerical transformation, so the
contrast preserves that existing policy.

Only full `tol_gap_rel` changes from 1e-10 to **1e-9** relative to the earlier
SuperLU configuration. Absolute gap, feasibility and reduced tolerances remain
1e-10; max_iter remains 5000 and min_terminate_step_length remains 1e-8.
Native `AlmostSolved` is not accepted. The SuperLU ordering, static shift,
equilibration and native refinement are unchanged; both compensated-arithmetic
switches and experimental KKT scaling are disabled.

The surplus native input is exactly the prior JSON payload except this one
setting. Both cases' original and transformed canonical matrices match their
retained substitution artifacts before any solver call. No retry, additional
solver arm, source edit during execution, or numerical rescue occurred.

## Results

| Measure | Surplus with fixed loads | Deficit with optional shedding |
| --- | ---: | ---: |
| Native status | Solved | Solved |
| Complete diagnostic acceptance | Pass | Pass |
| Iterations | 32 | 39 |
| Relative gap | 6.74077e-10 | 3.98802e-10 |
| Absolute gap | 2.10649e-9 | 6.14461e-4 |
| Native primal residual | 3.02165e-14 | 5.87033e-14 |
| Native dual residual | 5.31055e-16 | 2.89094e-14 |
| Reconstructed physical objective | 3.12499875193 | 1540766.72981407 |
| Canonical versus physical objective discrepancy | 7.20530e-11 | 1.40660e-5 |
| Independent physical audit | Pass | Pass |
| Native elapsed seconds | 25.31 | 31.71 |
| Worker elapsed seconds | 27.22 | 33.81 |
| Supervised wall seconds | 29.06 | 35.89 |
| Combined sampled RSS MiB | 884.5 | 928.5 |

CLARABEL accepts either the absolute or relative gap test, together with
primal/dual feasibility and the embedding condition. Both arms qualify through
relative gap; neither meets the retained 1e-10 absolute-gap target. The larger
deficit absolute gap reflects an objective around 1.54 million, not the same
absolute economic uncertainty as the surplus case. These are numerical solver
criteria and audited SOCP solutions, not exact-arithmetic optimality certificates
or recovered AC feasibility certificates.

Offline reconstruction rebuilds the original models without solving, assigns
the saved full canonical primals, reproduces public Pg/Qg/b/SoC/ND outputs,
recomputes physical audits and checks the native convergence inequalities.
The surplus x/s/z are bit-for-bit identical to the earlier SuperLU result.
Historical rejected artifacts retain their original status and acceptance.

## Python and cvxopf implementation

Separate mathematical preparation from solver internals:

1. Normalized device capability cones belong in the shared component model
   layer, preserving engineering-unit public results and exact device sets.
2. Exact fixed-coordinate elimination and joint canonical scaling belong in
   a solver-delivery transformation with explicit inverse maps for primal,
   dual, slack and objective offsets. The tested joint rule scales A, P, b and
   c consistently, uses common row scaling within each SOC, and applies no
   global objective multiplier. It must not be replaced by A-only balancing.
3. The alternative factorization belongs below the CVXPY/cvxopf boundary, in
   CLARABEL's native KKT implementation. Public `build.solve()` remains the
   solve entry point; physical acceptance remains a separate audit.

Today the experiment routes `build.solve()` through a scoped adapter hook to
a patched native executable, which sends linear systems to persistent Python
`scipy.sparse.linalg.splu`. It works as a diagnostic, but serializes each
factorization/solve request. About **19.0 / 24.0 seconds** of the respective
native runtimes are serialization, transport, startup, logging and other bridge
overhead; LU factorization plus triangular solves total **4.55 / 5.50 seconds**.
Those are measured components, not a production-speed prediction.

A maintainable implementation would use a separately packaged experimental
solver adapter initially, and preferably a supported native pivoted-factor
backend rather than a global monkeypatch or permanent JSON subprocess bridge.
Ship it as an explicit option with recorded configuration and original-unit
audits, not as a silent replacement of the installed solver. CLARABEL exposes
linear-solver selection, but the tested SuperLU bridge is a custom patch, not
a documented `direct_solve_method="superlu"` option. Backend availability is
build-dependent. See the [CLARABEL settings](https://clarabel.org/stable/api_settings/)
and the retained 0.11.1 source in `engine.json`.

The deficit case already converged with ordinary QDLDL at 1e-10 in the earlier
substitution study. Thus these results support an opt-in alternative or a
separately declared fallback, not replacing QDLDL for every solve. They also
do not establish that every preparation step is necessary. Before adoption,
qualify the integration and a broader representative set rather than silently
relaxing historical acceptance definitions.

## What transfers to MOSEK and COPT

The normalized device representation, exact substitution and cone-preserving
joint scaling are mathematical transformations and can be delivered to other
solvers with correct inverse mapping. Their numerical benefit need not be
uniform. Our previous three-solver termination study already supplied these
transformations to MOSEK/COPT; this is not a newly untried remedy for them.

The pivotal new success is solver-specific: replacing CLARABEL's QDLDL path
with SuperLU, then choosing a relative-gap target it reaches. A native KKT
replacement or compensated determinant patch does not transfer through an
ordinary MOSEK/COPT Python parameter. We have not localized the commercial
solvers' failures to the same arithmetic or linear-system defect.

MOSEK previously stalled with a physically acceptable primal but a retained
native task gap around 4.87e-6 at an objective near 3.125. This does not predict
success from changing its requested relative gap to 1e-9 alone. MOSEK has its
own feasibility/gap definitions and post-stall tolerance handling; compare
original-unit audits and native evidence, not parameter names alone. See
[MOSEK conic termination criteria](https://docs.mosek.com/latest/pythonapi/solving-conic.html).

COPT previously reported Optimal but failed original-unit balance and device
checks even at its supported 1e-9 FeasTol/DualTol floor. Loosening a gap target
cannot repair that physical-validation failure. Its own scaling, presolve and
barrier controls are a separate investigation, not implied by the present
CLARABEL result. See [COPT parameters](https://guide.coap.online/copt/en-doc/parameter.html).

The practical conclusion is one working CLARABEL path on the two tested
conditions, with credible reusable model preparation—not three qualified
solvers. No commercial-solver rerun was performed in this check.

## Evidence and verification

Runner: [practical_lu.py](practical_lu.py). Independent offline reconstruction:
[practical_lu_analysis.py](practical_lu_analysis.py). Tests:
`tests/test_socp_conditioning_practical_lu.py`. Execution and analyzer sources
are bound separately. The native source patch, entrypoint, compiler identity,
lockfile and binary are retained in `engine.json`.

Using the established isolated single-thread environment and
`uv run --offline --no-sync --with coptpy==8.0.7 --with mosek==11.2.5 python -B`:

```text
-m experiments.socp_conditioning.practical_lu
  --output experiments/socp_conditioning/results/practical_lu_001
  --reference /Users/bmeyers/github/cvxopf/experiments/case118_tracy_2021/results/e3

-m experiments.socp_conditioning.practical_lu_analysis
  --root experiments/socp_conditioning/results/practical_lu_001
  --reference /Users/bmeyers/github/cvxopf/experiments/case118_tracy_2021/results/e3
```

Existing outputs are immutable; reproduction needs a fresh root. Both workers
stayed within 180 seconds and 4096 MiB, including native/bridge descendants.

| Artifact under `results/practical_lu_001/` | SHA-256 |
| --- | --- |
| `summary.json` | `f77f329ac502daa9c70a68a5cb218132f4448d3950cab738ab2364ff6c52a1ce` |
| `analysis.json` | `27ceb1d9bab65e55d1dc17999d0db1631773fa643a0f18c856e0d567260c1545` |
| `surplus/arm.json.gz` | `0a8b245c558e1a199e33c157de8866a040343eed71e25ec5ce445586eebfe12d` |
| `deficit/arm.json.gz` | `2a8ea1edb22b2ca88d2535d52a952ee8aa341d93fba57328547ff12452814294` |
| `engine.json` | `f47dc8a73e23fa43c4c3a982487b2a8345bbfffc18be46b7677d9aa34929ff9d` |

All 162 conditioning tests pass, including explicit tolerance, mode isolation,
native-input identity and convergence regressions. The inherited OpenMP import
and boundary norm warnings remain. Work is isolated and unstaged/uncommitted;
production sources, installed solvers, lockfiles and main checkout are unchanged.
