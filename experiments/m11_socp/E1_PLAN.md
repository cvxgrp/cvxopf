# Standard SOCP reference suite — predeclared execution contract

2026-10-01. E1 only; E2 and Tracy are not authorized by this slice.

Fixtures: a deterministic two-bus resistive network with fixed source magnitude,
the exact repository Case9 and Case14, and existing algebraic triangle controls.
No optional devices, shedding, angle constraints/cuts, voltage-product bounds,
DC loss proxy, or objective regularizer in the optimized reference cases.
`sparsity_tol=0`, both-terminal ratings enabled, `delta=1`; setpoints disabled
for Case9/14 and enabled for the two-bus control. Generator P/Q boxes and
quadratic costs retain repository semantics.

Independent reference: PowerModels 0.21.5 `SOCWRConicPowerModel`, Clarabel.jl
0.11.1, JuMP 1.31.2, JSON 0.21.4, Julia 1.12.7. Record the full resolved manifest
and source hashes. Use its existing voltage, device, balance, Ohm's-law, thermal
and cost functions, omitting stock angle constraints/cuts and creating edge
products with `bounded=false`. Verify parser normalization against the original
case arrays before solving; do not accept silent model changes.

Generation and new-test budget: at most 24 optimization calls (reference and
Python combined), at most 20 seconds and 200 iterations each, hence at most
480 seconds solver time. Count failed calls too. Stop and diagnose a failed
check; rerun only affected checks within this ceiling, never enlarge it.
At most 15 minutes environment preparation/precompilation, measured from first
instantiation. Existing regression tests retain their previously declared
settings and are separate from this new E1 generation/test allowance. Run one
full regression suite at the final checkpoint. No new AC solve is needed for E1.

Both solver implementations: absolute/relative objective-gap and feasibility
tolerances `1e-9`, `max_iter=200`, `time_limit=20`. Acceptance: numerical optimum
estimates agree to `rtol=2e-6`, `atol=2e-5` objective units. Reconstruct network
physics independently using direct branch coefficients (not builder sparse
maps or constraint violations): P/Q balance and terminal powers `1e-4`
MW/MVAr/MVA, voltage/cone residual `1e-6` p.u.^2, objective accounting `rtol=1e-10`,
`atol=1e-7`. Reuse declared diagnostic/recovery thresholds in `PLAN.md`; do not
require meshed recovery or matching nonunique dispatch. Compare the unique
two-bus primal to its analytic solution at `1e-5` MW and `1e-6` p.u.^2.

`max_cone_violation` is the positive norm-based SOC residual
`max(0, norm([2*Re(W), 2*Im(W), wi-wj]) - (wi+wj))`, in p.u.^2.
Record negative determinant gap separately as `max_determinant_violation`
in p.u.^4; it is not the residual tested against the SOC tolerance.

Commit compact, provenance-bearing JSON fixtures only after these checks pass.
Raw requests, parser output and logs go in ignored `results/e1/`. CI consumes
static JSON fixtures under `tests/fixtures/`; permanent cases and oracle helpers
live under `tests/`, while reusable generation tooling and its pinned Julia
environment live under `scripts/socp_reference/`. The experiment retains this
execution plan, reports, selected evidence, analysis and raw outputs. CI needs
no Julia, network access, downloads or regeneration;
missing/stale fixtures fail rather than skip. Stop for owner review after the
suite, report all discrepancies and budget usage, and do not claim M11 closure
or certified lower bounds.
