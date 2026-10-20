# Standard SOCP references — E1 checkpoint

2026-10-01. E1 implementation/evidence checkpoint for owner review, not M11
closure. E2 has not begun and Tracy remains on hold. No production model or
solver policy changed in this slice.

## Scope and model matching

The standard suite now includes a two-bus analytic optimum, independently
generated PowerModels Case9/14 fixtures, and the existing algebraic triangle
controls. CI reads static public fixtures and needs no Julia or external service.
Missing/stale references fail; no new skip was introduced.

PowerModels 0.21.5 `SOCWRConicPowerModel` uses its own affine Ohm's-law, balance,
voltage, generator, cost and conic thermal functions. The reference builder
explicitly omits angle-difference constraints/cuts and calls the edge-variable
constructor with `bounded=false`. A structural check confirms no edge-product
box bounds. This matches the basic sparse voltage-product relaxation rather
than stock PowerModels strengthening. Production physics were not changed to
match the reference.

The exact repository bus/gen/branch/gencost arrays and baseMVA are embedded and
hashed in each fixture; no PGLib substitutions. PowerModels parses with
`validate=false` to avoid its correction heuristics, then performs only explicit
per-unit conversion. Before solving, checks verify every modeled voltage bound,
load/shunt, generator P/Q limit and cost coefficient, branch endpoint/status,
impedance, tap/phase, charging and rating against the original input. Recorded
normalizations are zero tap to unity, equal charging split, zero rating omission,
load/shunt separation, and unit conversion of powers, costs and angles.
Setpoints are disabled for Case9/14 and enabled for the analytic control.
There are no optional devices, shedding, angle strengthening or loss-cost proxy.

The independent Python oracle reconstructs complex branch coefficients and
nodal sums directly from input tables. It does not consume builder sparse maps,
constraint violations or cached reference residuals. Both external and returned
primals pass balance, voltage, generator, both-terminal rating and cone checks.
Deliberate corruption of products, generation or terminal power fails the oracle.
The package's separate numerical audit also accepts each external primal.

## Analytic control

The two-bus network has baseMVA=100, resistance `r=0.1` p.u., no reactance,
charging or shunts, and receiving load `p=0.1` p.u. with zero reactive demand.
The source magnitude is fixed at 1 p.u.; both voltage ranges are 0.9–1.1 p.u.
Generator P/Q bounds and the 100-MVA terminal limits are nonbinding at the
optimum. Its strictly increasing cost is `0.01*Pg_MW^2 + Pg_MW` for `Pg>=0`.

Reactive balance requires `Im(W12)=0`. Receiving real balance gives
`c=w2+r*p`, and the cone requires `(w2+r*p)^2 <= w2` because `w1=1`.
Since `Pg=(1-c)/r` decreases with `w2`, the upper quadratic root is optimal.
It is attained by the physical positive real voltage
`V2=(1+sqrt(1-4*r*p))/2`. This establishes the optimum independently of the
builder and verifies exact recovery for this particular case, not for radial
networks in general.

The analytic dispatch is approximately 10.10205144 MW: 10 MW served demand
plus 0.10205144 MW physical line loss. The objective is approximately
11.12256588. The optimized unique primal agrees within the predeclared tolerances
in all three assembly paths, and both analytic and optimized candidates recover
AC-feasible voltages without a repair solve.

## Objective agreement and recovery

Single-step values below; vectorized T=1 and explicit stepwise T=1 also pass.
Numbers are numerical optimum estimates, not certified lower bounds.

| Case | PowerModels objective | cvxopf objective | Absolute difference | External primal physical loss (MW) | Exact/AC-feasible recovery |
| --- | ---: | ---: | ---: | ---: | --- |
| Two-bus | 11.1225658722 | 11.1225658768 | 4.58e-9 | 0.10205144 | Yes / Yes |
| Case9 | 5296.6660833080 | 5296.6660834055 | 9.76e-8 | 3.30573036 | No / No |
| Case14 | 8075.1247002672 | 8075.1247003662 | 9.90e-8 | 9.17791006 | No / No |

Acceptance was predeclared at objective `rtol=2e-6`, `atol=2e-5`; no tolerance
was changed after observing results. Actual external primal balance residuals
are at most 3.81e-9 MW/MVAr and norm-based SOC residuals at most
6.93e-10 p.u.^2. Negative determinant violations are reported separately,
at most 7.65e-10 p.u.^4. Returned Python balance residuals are at most 1.72e-10
MW/MVAr; all physical checks pass their declared thresholds.

External Case9 has maximum normalized edge gap 0.00728 and cycle inconsistency
0.00525 radians; its recovered maximum P/Q balance error is 8.73 MW/MVAr.
Case14 is edge-tight (maximum normalized gap 1.67e-9) but cycle-inconsistent
(0.05872 radians); recovery gives 108.33 MW/MVAr maximum balance error.
The Python solves show the same qualitative classifications. These are failed
recoveries of feasible relaxed primals, not evidence of AC infeasibility.
Individual nonunique meshed voltages/reactive dispatch and recovery errors are
not required to be identical across implementations.

Fixtures distinguish the solver objective from cost evaluated at the complete
primal. PowerModels' conic cost epigraph and numerical termination need not
yield bitwise-identical values; the largest observed difference is 2.04e-7
cost units. Objective components store the independently evaluated primal cost.
Generation/loss accounting is physical; no DC proxy enters this comparison.

## Timing, environment and budget

Host: macOS 26.7, arm64; Python 3.11.15, CVXPY 1.9.3, Clarabel Python 0.11.1,
NumPy 2.4.6. Reference: Julia 1.12.7, Clarabel.jl 0.11.1, JuMP 1.31.2,
PowerModels 0.21.5, InfrastructureModels 0.7.9, JSON 0.21.4. The full dependency
manifest is pinned in `scripts/socp_reference/reference_env/`; fixtures record package tree hashes,
generator/manifest hashes and relevant PowerModels source hashes.

The isolated depot is in ignored `results/e1/julia_depot`; the global Julia
project was not changed. Package setup/imports completed within the declared
15-minute ceiling. Restricted-network instantiation failed before downloading;
the authorized network retry installed the pinned packages without disabling
TLS verification. No optimization call failed.

Python scalar variable counts are 6 / 33 / 64 for two-bus / Case9 / Case14.
Network cones are respectively 1 / 9 / 20 of dimension 4, plus 2 / 18 / 40
terminal-rating cones of dimension 3. Python builds take roughly 1.7–3.2 ms,
canonicalization 5.4–14.8 ms, and recorded solver time 0.078–1.009 ms across
the nine focused solves. PowerModels first-case build/solve wall times are
1.42/3.23 seconds (cold compilation), versus 0.0026/0.0049 seconds for Case9
and 0.083/0.0074 seconds for Case14. These small, partly cold measurements are
not a cross-language speed or long-horizon scaling claim.

E1 optimization calls: 3 independent reference solves, 9 focused Python solves,
and 9 new-test solves in the full regression run: **21 of 24 allowed**. Existing
regression solves use their established settings separately. Algebraic audits,
fixture acceptance and evidence summarization perform no optimization. All E1
calls use 200 iterations / 20 seconds maximum and `1e-9` feasibility/gap settings.
Execution stops at this checkpoint, with no case expansion or E2 solves.

## Reproduction and verification

From the repository root, ordinary CI verification requires only:

```bash
uv run --extra dev pytest tests/test_socp_reference.py tests/test_socp_diagnostics.py -q
```

Offline regeneration requires the pinned Julia version and manifest; it is not
a CI step. Set `JULIA_DEPOT_PATH` to an isolated writable depot before running
`Pkg.instantiate(; update_registry=false)` against
`scripts/socp_reference/reference_env`. From the repository root:

```bash
uv run --extra dev python -m scripts.socp_reference.export_inputs --raw-dir experiments/m11_socp/results/e1
julia --startup-file=no --history-file=no --project=scripts/socp_reference/reference_env scripts/socp_reference/generate_references.jl experiments/m11_socp/results/e1
uv run --extra dev python -m scripts.socp_reference.accept --check --raw-dir experiments/m11_socp/results/e1
```

The acceptance script refuses accidental fixture replacement. On initial
creation only, omitting `--check` publishes fixtures after all audits pass.
Selected measurements/diagnostics are in [E1_EVIDENCE.json](E1_EVIDENCE.json);
the raw focused JUnit record stays in ignored `results/e1/focused.xml`.
Regenerate that evidence with:

```bash
uv run --extra dev pytest tests/test_socp_reference.py tests/test_socp_diagnostics.py -q -o junit_family=xunit1 --junitxml=experiments/m11_socp/results/e1/focused.xml
uv run --extra dev python -m experiments.m11_socp.summarize_references
```

Verification: focused suite **36 passed** (16 new reference tests,
20 existing algebraic controls); repository lint, explicit lint of the new
experiment Python helpers, and configured mypy pass. Full regression:
**3,347 passed, 6 subtests passed** in 339.90 seconds; 119 existing-category
warnings (OpenMP imports, device fallback/non-applicability, DPP and matplotlib
deprecation), no failures or skips. Fixture regeneration-check mode also passes
without replacing the accepted files. `git diff --check` passes.

Review correction: `max_cone_violation` now measures the declared norm-based
SOC residual in p.u.^2; `max_determinant_violation` retains the separate negative
determinant measurement in p.u.^4. Fixture audit metadata and selected evidence
were recomputed from the unchanged external primals, without optimization.
Historical Python summaries lack saved edge primals, so their SOC residuals are
explicitly unavailable; their original determinant measurements are retained
under the correct name. Raw records, objectives, timings and the 21-call budget
count are unchanged. Correction verification: **28 solver-free tests passed**,
9 optimization tests deliberately deselected; fixture regeneration checks,
targeted lint and `git diff --check` pass. A scaling regression distinguishes
the two residual definitions and their units.

Ownership correction: permanent analytic cases and independent audit helpers
now live under `tests/`, and reusable generation/acceptance tooling and the pinned
Julia environment under `scripts/socp_reference/`. The JSON fixtures remain in
`tests/fixtures/`. The exact original generator is preserved at
`scripts/socp_reference/provenance/e1_generate_references.jl` so its recorded
hash is still verifiable; the maintained generator changes only raw-directory
selection, not reference equations or solver settings. The manifest, fixture
JSON, selected numerical evidence and raw outputs were not rewritten during
relocation. Historical sources are provenance, not regeneration entry points.
See [maintenance instructions](../../scripts/socp_reference/README.md).
Relocation verification: **32 solver-free tests passed**, 9 optimization tests
deselected; acceptance checks, targeted lint and Julia syntax parsing pass.
The relocated analysis summarizer reproduces the selected evidence byte-for-byte.
No optimization calls or new numerical evidence were added.

Reproducibility correction: fixture `--check` now independently audits both
stored and regenerated primals against the declared feasibility thresholds,
retaining objective and provenance comparisons but removing cross-run residual
equality. The analytic two-bus regression permits floating-point roundoff and
compares against a deliberately perturbed candidate in a temporary fixture,
independently feasible within declared thresholds. Its controlled nonzero
SOC/determinant residuals differ from the analytic candidate without depending
on historical fixture roundoff. Both supported source records pass; accepted
repository fixtures are never modified by this test.
Verification: **34 solver-free tests passed**, 9 optimization tests deselected;
fixture checks, targeted lint and `git diff --check` pass. Accepted fixtures,
numerical evidence and raw execution outputs are unchanged; no new solves.

The remaining next slice is E2's matched AC/SOCP configuration validation,
feasible-AC lifting, equal objective-component accounting and one small
multistep storage/shedding comparison. Independent oracle agreement does not
establish those complete-model containment contracts or close M11.
