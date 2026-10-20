# E1 regression after numerical preparation — execution contract

2026-10-08. The owner approved preparing this runner. Numerical execution remains
separately authorized after review and a clean commit. This is a fresh Python
regression against the existing external oracles, not reference regeneration or
replacement of [historical E1](E1_REPORT.md). E2, E3 and Stage D are not launched
by this checkpoint.

## Matrix and fixed numerical choices

Reuse the exact two-bus, Case9 and Case14 inputs and immutable PowerModels
fixtures under `tests/fixtures/`. For each case, compare baseline and prepared
SOCP in single-step, vectorized T=1 and stepwise T=1 assembly: **18 calls**,
ordered by case, assembly, then baseline/prepared. These are the original nine
Python comparisons with an additional representation treatment, not new physics.

Baseline uses disabled `NumericalPreparation`. Prepared uses normalized device
limits, exact fixed boxes and `canonical_scaling="joint5"`, keeping original
economic coordinates and hourly objective assembly. There are no storage,
renewable, shedding or HVDC devices in E1; normalization is therefore inert and
these cases do not qualify device normalization or cycling/shedding accounting.
Record the actual fixed-coordinate count rather than assuming reduction occurs.
Prepared device/accounting coverage belongs to the separately planned E2 refresh.

Keep the original E1 solver limits/checks: CLARABEL `max_iter=200`, native
`time_limit=20`, `tol_gap_abs=tol_gap_rel=tol_feas=1e-9`. Both treatments explicitly
use stock QDLDL, one numerical thread and no warm start; record all resolved stock
CLARABEL settings before execution. These reference-regression settings are not
a replacement for the separately qualified Tracy solver settings. No tuning,
alternate treatment, retries, case expansion or automatic setting changes.

## Acceptance and evidence

Reuse `tests/socp_reference_comparison.py` for live checks, historical baseline
CI checks and non-solving archive replay. Reference provenance requires exact
case arrays/input hashes, matching physical policies, the pinned PowerModels
implementation and unchanged generator/manifest sources. Missing/stale references
fail; no Julia, downloads, regeneration or substitute inputs.

Each candidate must return native full `Solved` and public `optimal`, agree with
the reference optimum at the original `rtol=2e-6`, `atol=2e-5`, and pass the
independent original-table physics/cost oracle and package relaxation audit.
Retain the original `pytest.approx` allowance semantics (maximum of absolute
and relative allowances), not a looser sum. Physical balance and signed terminal
checks remain `1e-4` MW/MVAr/MVA, voltage/norm-cone residual `1e-6` pu²,
objective accounting `rtol=1e-10`, `atol=1e-7`; determinant residual remains
separate in pu⁴. Require correct product orientation. Preserve the unique
analytic two-bus primal/recovery checks. Meshed recovery failure and nonunique
dispatch differences are observations, not automatic rejection or AC infeasibility.

Additionally retain native primal/dual vectors and costs, preparation maps,
scale factors and restoration diagnostics. Prepared evidence must be present;
maps, scales, offsets and restoration diagnostics must match independent
reconstruction from the fresh canonical graph. Restore retained native primals
through that graph's inverse chain and compare physical fields to the archive
at `1e-10` absolute tolerance (a mapping-identity check, not a feasibility gate);
baseline must have no preparation evidence. Restore substitution and CVXPY
objective constants before independently comparing native cost to the physical
generator cost, using the same original accounting allowance. Production mapping
unit tests remain separate; this is not a claim that every dual/scale coordinate
has an independent external reference. No native primal estimate is called a
certified lower bound.

Freeze source/environment, fixture/plan hashes, physical input hashes, numerical
policies and original canonical signatures before optimization. Record one
optimizer call per worker, request/launch/phase/log/RSS/supervision, full physical
result/native evidence and hashed completion manifests. Only finalized normal
workers within their budgets can be accepted. Read-only `--status` reconstructs
inputs/canonical signatures and replays independent physical/cost acceptance
from the retained primals without optimization. It requires the original clean
execution context; no source-binding bypass or resume.

## Resources and stopping

Fresh ignored output defaults to `results/e1_regression_001`. Existing output
directories are refused. Original E1/E2 evidence, fixtures, plans, reports and
the legacy summarizer remain unchanged; no compact historical JSON is overwritten.

At most 18 launches and 18 optimization calls, each with a 20-second native
limit. The serial shared supervisor allows **30 seconds per entire worker**
(imports, construction, solve and audit), **540 cumulative worker-seconds** and
**16 GiB sampled RSS**, with 0.1-second resource sampling. This is a new budget,
not unused allowance claimed from historical E1. Numerical library threads are
one. Verify process/RSS access, AC power and thermal telemetry before invocation
and every launch; missing telemetry stops without bypass.

Stop on the first unaccepted outcome, including numerical/audit rejection,
wall/RSS limit, worker/monitoring failure, interrupted attempt or operator STOP.
Count effort and preserve partial artifacts; do not advance an unfinished attempt
or infer health from a live PID. Cumulative budget exhaustion also stops.
No fallback, retry or treatment change is authorized. Completed archives,
finalized supervision and accepted cases are separate counts. Finish for owner
review; a clean E1 regression does not close M11 or authorize later gates.

```sh
uv run --extra dev python -m experiments.m11_socp.e1_compare --preflight
# Only after reviewed clean commit and separate execution approval:
uv run --extra dev python -m experiments.m11_socp.e1_compare --authorize-execution --commit <full SHA>
uv run --extra dev python -m experiments.m11_socp.e1_compare --status
```
