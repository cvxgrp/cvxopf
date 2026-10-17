# Fixed-load ablation: three solver paths

2026-10-02. Same Case118 surplus window [3308,3332), normalized device cones,
original paired generator/renewable bounds, storage endpoints and unscaled costs.
Only the optional load-shedding policy was removed: every load has
`shedding_cost_per_mwh=None`. Active/reactive demand remains unchanged. There are
no shedding variables, penalty expression or added zero-width shedding bounds.

Three serial fresh workers used the preceding solver settings: CLARABEL 0.11.1
at 1e-10 tolerances; MOSEK 11.2.5 and COPT 8.0.7 at default numerical tolerances.
One thread each; 180 seconds/4 GiB per worker. All completed inside those limits,
without retries. This is a same-solver ablation, not equal-tolerance benchmarking.

## Results

| Quantity | CLARABEL | MOSEK | COPT |
|---|---:|---:|---:|
| Native status | AlmostSolved | Unknown | Optimal |
| Iterations | 36 | 36 | 41 |
| Native solve seconds | 0.842 | 1.162 | 1.782 |
| Extracted physical objective | 3.125903472 | unavailable | 3.124994687 |
| Canonical primal objective | 3.125903834 | 3.125471027* | 3.125004693 |
| Canonical dual objective | 3.125895386 | 3.125416243* | 3.12500449 (log) |
| Canonical/physical discrepancy | 3.616e-7 | unavailable | 1.001e-5 |
| Original-unit physical audit | Pass | unavailable | Narrow rejection |
| Strict diagnostic acceptance | No | No | No |

*MOSEK's dualized maximization task retained these native objectives, mapped to
the original orientation (zero offset). Its status was Unknown, so CVXPY raised
SolverError and did not extract a physical primal. These are numerical iterates,
not accepted bounds or an audited physical solution. Native task vectors remain
available in the archive; no extra solve or acceptance override was used.

CLARABEL passes every physical/accounting test and canonical/physical cost
agreement; only the stricter native-Solved requirement prevents full diagnostic
acceptance. Its active/reactive balance residuals are 5.07e-12 MW / 2.56e-11 MVAr.
SoC recurrence is 3.44e-13 MWh and terminal error 1.14e-13 MWh.

COPT passes cost agreement. Its only physical quantity above tolerance is active
balance, 1.0257501e-4 MW versus 1e-4 MW (also reported by the injection check).
That is about 102.6 W against a 100-W threshold, not a large physical defect.
Reactive balance is 7.69e-5 MVAr; recurrence is 2.06e-7 MWh; terminal error is zero.
All thresholds were retained unchanged. Served P/Q exactly equal the prescribed
inputs for both extracted solutions.

CLARABEL's costs are storage 3.125903319 plus generator 1.53655e-7. COPT's are
storage 3.125002198 plus generator -7.51066e-6. Thus removal of shedding eliminates
its artificial negative cost, but small numerical bound effects can still appear
in other cost terms; the reported values are not exact optima.

## Interpretation against the preceding tests

With shedding available, physical objectives were CLARABEL 3.122147605,
MOSEK 23.492277916 and COPT 3.116113562. The MOSEK canonical objective was 23.7772;
it is now near 3.1254. CLARABEL/COPT's fixed-load physical objectives differ by
about 0.000909, or 0.029%, with much better canonical/physical agreement.

This supports optional shedding/its large penalty as a material contributor to
the observed numerical sensitivity. The intervention removes both the extra
variables/constraints and their penalty; it does not isolate those two effects.
It does not establish a universal cure, infeasibility of the shedding model,
or that shedding can be disabled safely for deficit scenarios. It changes the
feasible set, even when a zero-shedding solution exists. No production change or
general fallback policy is being selected from this single surplus comparison.

## Audit adaptation and provenance

The raw fixed-load public schema intentionally omits shedding and ENS outputs.
For reuse of the historical all-sheddable audit only, those absent quantities
are explicitly projected to zero. Actual served P/Q and every solved network/
device quantity are left untouched. A dummy coefficient multiplies exact zero
only inside accounting reconstruction, never in the model. This is recorded in
each audit; raw results remain unmodified. Structural and projection tests verify
the intervention does not silently zero real result fields.

All artifacts are in `results/no_shedding_001`. Input identity is shared across
all three arms:
`15369f209ea6a41459fc1b50a19d47c1b76b84f4cc27fd02529b3504e57ba024`.
Each arm also binds the original with-shedding input and E3 archive. Native
arrays, CLARABEL canonical matrices, MOSEK/COPT task files, costs, audits and
logs are retained. Every registered artifact hash and before/after source
context was verified. Production and live environment are unchanged.

Arm SHA-256 identities:

- CLARABEL: `d19023a111b3efe629df4b77d499bbea8c9ba2c544772f70ad18d7f26f1dca2a`
- MOSEK: `849cb654330c283a60b32d222861fc9a55100202fe1ba6fbb4e86a8d71f99621`
- COPT: `f69f7e172f74f78a3e456e9763eaac59bc27b99d87d39ee17fd0df876ba2d7af`

Supervised wall times were 3.640/3.641/5.205 seconds and sampled peak RSS
398.3/467.7/529.6 MiB. Execution remains an explicitly uncommitted isolated
diagnostic on base dc1c5ace2d787fda563f99c0d3c11a48ba3e9b6a, not promotion.
