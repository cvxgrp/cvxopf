# SOCP reference fixture maintenance

Reusable offline generation and acceptance tools for the standard Python
regression suite. Analytic cases and the independent numerical oracle live in
`tests/socp_reference_cases.py` and `tests/socp_reference_audit.py`; accepted
JSON fixtures live in `tests/fixtures/`. No test imports the historical experiment.
CI requires neither Julia nor downloads and does not regenerate fixtures.

## Regeneration

Run from the repository root using Python's module entry points. Supply an
explicit raw directory in the relevant experiment's ignored `results/` tree.
For the original E1 checkpoint, the commands are:

```bash
uv run --extra dev python -m scripts.socp_reference.export_inputs --raw-dir experiments/m11_socp/results/e1
julia --startup-file=no --history-file=no --project=scripts/socp_reference/reference_env scripts/socp_reference/generate_references.jl experiments/m11_socp/results/e1
uv run --extra dev python -m scripts.socp_reference.accept --check --raw-dir experiments/m11_socp/results/e1
```

Generation overwrites files in the supplied raw directory; use a fresh directory
to preserve historical runs. These commands describe the workflow, not authority
to rerun a closed study. New runs need their own declared budget and output path.

Use Julia 1.12.7 and the committed `reference_env/Project.toml` and
`Manifest.toml` (PowerModels 0.21.5, JuMP 1.31.2, Clarabel.jl 0.11.1, JSON 0.21.4).
Set `JULIA_DEPOT_PATH` to an isolated writable depot before instantiating:

```bash
julia --startup-file=no --history-file=no --project=scripts/socp_reference/reference_env -e 'using Pkg; Pkg.instantiate(; update_registry=false)'
```

Python helpers use the project environment. Export writes exact repository
MATPOWER arrays and hashes, without solving. Julia verifies parser normalization
and solves the unstrengthened voltage-product relaxation: no angle constraints,
angle cuts or rectangular product bounds. Generator P/Q limits and quadratic
costs, physical admittances, voltage bounds and both-terminal ratings remain.
No optional devices, shedding, regularizer or DC loss proxy are included.
Settings and case policies are shared with tests, not copied from the experiment.

Acceptance reconstructs numerical physics independently from original tables,
checks objectives and the unique analytic two-bus solution, and publishes only
after every check passes. It refuses existing fixture replacement. `--check`
independently audits both regenerated and stored primals against feasibility
tolerances, retains the objective comparison, and never requires cross-run
residual equality or writes fixtures. Solver roundoff and nonunique primals may
differ. Missing/stale source or input hashes fail. Tolerances remain objective
`rtol=2e-6`, `atol=2e-5`, power residuals `1e-4` MW/MVAr/MVA and voltage/SOC
residuals `1e-6` p.u.^2. Determinant violations are separate p.u.^4 measurements.
Objective estimates are not certified lower bounds; failed voltage recovery is
not an AC infeasibility certificate.

## Preserved provenance

`provenance/e1_generate_references.jl` preserves the exact generator bytes used
for the accepted E1 fixtures. It is a historical source record,
not a supported entry point. Its SHA-256 remains
`7b4ae48716b60075598c9a39c3f76305cddc33be503590aac8ba5735ea719877`.
The relocated manifest is likewise byte-identical. Fixture provenance checks
accept the hash of either this snapshot or the maintained generator; they do
not replace an old source hash with a new one. The maintained generator differs
only in its comment and explicit raw-directory argument. Numerical fixtures,
timings and raw execution records are unchanged by the ownership correction.

Execution plans, reports, selected evidence, experiment-specific analysis and
raw execution outputs remain under `experiments/m11_socp/`. Its summarizer now
imports permanent helpers and runs via
`uv run --extra dev python -m experiments.m11_socp.summarize_references`.
