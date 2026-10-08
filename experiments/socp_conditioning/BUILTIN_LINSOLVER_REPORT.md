# Built in CLARABEL linear solver results

The installed Faer backend does not reproduce the custom SuperLU convergence
improvement on the difficult surplus problem. QDLDL and Faer both stop near
9.37e-8 relative gap, above the declared 1e-9 target. Both pass the contrasting
deficit case. Every returned primal passes independent physical residual checks;
full acceptance still requires native Solved under this experiment's protocol.

## Controls and available backends

Executed 2026-10-06 under
[BUILTIN_LINSOLVER_PROTOCOL.md](BUILTIN_LINSOLVER_PROTOCOL.md). Four fresh serial
single-thread workers use the installed, unmodified CLARABEL 0.11.1 Python
wheel, explicitly selecting QDLDL and Faer on the two problems from the
[practical SuperLU check](PRACTICAL_LU_REPORT.md).

Constructor-only probes confirm QDLDL and Faer are available. Panua's compiled
interface reports its external solver unavailable. MKL is not enabled in this
ARM wheel. No solve was attempted with either unavailable backend, and no
library/license/environment installation was performed. Auto was not tested
as a separate treatment; the actual backend is retained in every solve record.

Native input arrays, cone order and settings exactly match the corresponding
practical-check input except `direct_solve_method`, verified before solving.
Normalized device cones, joint scaling and exact fixed-coordinate substitution
remain unchanged. The surplus window [3308,3332) retains fixed loads; the
deficit window [1165,1189) retains optional shedding. Relative-gap tolerance is
1e-9; absolute-gap and feasibility tolerances remain 1e-10. There are no
custom native patches, SuperLU bridge calls or compensated determinant changes.

## Matched outcomes

| Condition | Backend | Native status | Relative gap | Physical residual checks | Full acceptance | Native seconds |
| --- | --- | --- | ---: | --- | --- | ---: |
| Surplus | QDLDL | InsufficientProgress | 9.36376e-8 | Pass | Reject | 0.755 |
| Surplus | Faer | NumericalError | 9.36910e-8 | Pass | Reject | 0.708 |
| Deficit | QDLDL | Solved | 3.55834e-10 | Pass | Accept | 0.946 |
| Deficit | Faer | Solved | 3.75582e-10 | Pass | Accept | 0.896 |

Both surplus arms report iteration 31; both deficit arms report iteration 39.
All native feasibility residuals are below 1e-10. Surplus absolute gaps are
2.92617e-7 and 2.92784e-7; deficit absolute gaps are 5.48257e-4 and 5.78685e-4.
The deficit objective is about 1.54 million, so relative and absolute accuracy
must not be conflated across conditions.

For comparison, the previous custom SuperLU path achieved surplus relative
gap 6.74077e-10 and native Solved, at 25.31 seconds including its large bridge
overhead. These new runs are not slower versions of the same factorization:
Faer uses an LDL factorization, whereas the tested SuperLU path changes ordering,
factorization and pivot treatment as a package. The results do not isolate
which part of that package is necessary.

The Faer implementation inspected in CLARABEL 0.11.1 uses AMD ordering and
dynamic LDL regularization. It is not an interchangeable implementation of our
COLAMD, pivoted SuperLU path. The similar plateau is consistent with this
distinction, but the stock wheel does not retain the detailed KKT/cone failure
trace needed to attribute Faer's final NumericalError to a specific operation.

## Physical differences and timing

Compared with the accepted SuperLU surplus result, the QDLDL/Faer physical
objectives differ by only 1.11e-8 / 1.76e-8 cost units. Maximum battery power
and SoC differences are 0.0173 / 0.0596 MW and MWh, respectively. Faer's
renewable real-power difference is at most 0.00397 MW. Its relaxed voltage
magnitude difference is at most 1.92e-5 pu. Native rejection here is not a
demonstration of materially poor physical dispatch.

For deficit, objectives differ from SuperLU by -1.38e-5 / -6.31e-6 cost units.
Maximum battery-power differences are 0.142 / 0.417 MW; SoC differences are
0.138 / 0.417 MWh. No coordinate-equality gate is imposed in weakly identified
directions. SOCP feasibility remains distinct from full-physics AC realizability.

Canonical/physical objective reconstruction errors are about 1e-8 for surplus
and 1.3e-5 for deficit, all below the unchanged 1e-4 gate. Worker times range
2.22–2.49 seconds; supervised wall times 4.16–4.67 seconds. Peak sampled RSS
ranges 472.4–488.4 MiB, below the 4096-MiB limit; the wall limit was 180 seconds.
Single observations do not establish a robust Faer timing advantage.

## Disposition

Among the backends actually available and tested here, Faer is not a stock
replacement for the custom SuperLU remedy on the surplus case. Pardiso remains
untested, not numerically rejected. These results warrant neither a new solver
installation nor additional tolerance changes without a separate decision.

The [pending acceptance decision](ACCEPTANCE_DECISION.md) is now recorded and
linked from the study index. It asks how to separate independently assessed
primal usefulness, optimality evidence, native termination and AC realizability
before production adoption. Existing native-success requirements and historical
classifications remain unchanged. The proposed bounded AC conditioning comparison
remains separate; it was not started as part of this backend test.

## Evidence and verification

Runner: [builtin_linsolvers.py](builtin_linsolvers.py). Offline reconstruction:
[builtin_linsolvers_analysis.py](builtin_linsolvers_analysis.py). Tests:
`tests/test_socp_conditioning_builtin_linsolvers.py`.

The analyzer verifies the artifact chain and restores the saved full canonical
primals before extracting and independently auditing physical results. An
initial offline comparison from JSON lists differed from archived shedding
reporting residuals at approximately 1e-26 because array layout and reduction
order were lost. Restoring the original canonical-variable layout reproduces
the audits exactly. No solver rerun or raw artifact rewrite was needed.

Use the existing isolated, single-thread environment with
`uv run --offline --no-sync --with coptpy==8.0.7 --with mosek==11.2.5 python -B`:

```text
-m experiments.socp_conditioning.builtin_linsolvers
  --output experiments/socp_conditioning/results/builtin_linsolvers_001
  --reference /Users/bmeyers/github/cvxopf/experiments/case118_tracy_2021/results/e3

-m experiments.socp_conditioning.builtin_linsolvers_analysis
  --root experiments/socp_conditioning/results/builtin_linsolvers_001
  --reference /Users/bmeyers/github/cvxopf/experiments/case118_tracy_2021/results/e3
```

Reproduction requires a fresh output root. The installed extension SHA is
`2f1df54fdd8828c2acc4a55023a03d6a38d05c58ff398e541d68828c72e10f24`.
Exactly four optimization calls ran. No source changed during execution.

| Artifact under `results/builtin_linsolvers_001/` | SHA-256 |
| --- | --- |
| `summary.json` | `7726fc068b7d322d15bd2563f246e506c4962e1936b673acd86574a65cdb3dc5` |
| `analysis.json` | `70d6db2d7028c9d18d0f2ba68312944969ca7fdd0d251d420db9c8d0da04c488` |
| `availability.json` | `f02d8926d81f22e948e54573e21c76dd7245eeb6a1b4a504fe94598ea05c9f87` |
| `surplus-qdldl/arm.json.gz` | `97632182f56d629299f04a36c0fe7daa2ec3619ca4957a46fca55a7707c1ed04` |
| `surplus-faer/arm.json.gz` | `a88d475d9cde59d9e374db69e5aac11c6546464d31e3362ce74f4ad374864f73` |
| `deficit-qdldl/arm.json.gz` | `685850daaa4a040f9336c160d1a458d1e85db88e17b6ebfe92deb628dedc45ba` |
| `deficit-faer/arm.json.gz` | `f64bf5aca3331f9dae5a0e0d1788dbf49fa3de91599925b7bd7fcc6925eaf23a` |

Verification: 166 conditioning tests, Ruff lint/format and whitespace checks
pass. Known OpenMP import and zero-norm test warnings remain. No production
source, installed solver or main checkout changed. Work remains isolated,
unstaged/uncommitted and nonpromotional.
