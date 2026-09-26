# Time-vectorized inner AC controller integration

Owner-authorized production integration of recovery capability exercised in the
completed 120+6 replay. Implement in this checkout; no staging, commits or pushes.

## Boundary

A single private AC start-mapping module serves the replay worker and reusable
controller. It maps between historical per-step physical coordinates and the
actual time-last leaves without constructing a second optimization graph.
The initial SoC boundary is supplied separately and excluded from perturbations.
Existing shift and seeded perturbation algorithms operate in historical logical
coordinate order. Copying target-free starts retains all physical coordinates.

Add independent public `inner_temporal_assembly`, vectorized by default with
explicit stepwise override. Preserve the outer selector, sparse-dispatch policy,
recovery order/seeds, source eligibility, terminal policies, acceptance and
result schemas. Complete canonical IPOPT x0 capture/verification, including
introduced auxiliary variables, remains on every native attempt. Historical
experiment entry points explicitly retain their original representation;
retained manifests and evidence bytes remain untouched.

## Bounded validation

- Round trips and projection in both sparse/dense AC P/Q layouts; native shapes,
  initial SoC boundary, deterministic perturbation draw order, shifted tails,
  shrinking windows, and identity-aligned multi-storage state.
- Sequential Case9 trajectories with five steps and three-step AC windows under
  both representations and hard/soft terminal policies. Verify state recurrence,
  accepted-source advancement, first-action execution, and full x0 evidence.
- Controlled rejection of native attempts to exercise copied target-free,
  perturbed target-free and perturbed causal acceptance, followed by advancement
  and shrinking final windows. No change to the recovery policy itself.
- Full regression suite, Ruff/mypy, and independent scientific/public-API review
  by `cvxopf-review`, including duplication and technical-debt assessment.

No new large study, universal performance claim, or identical-nonconvex-solution
requirement. Stop at a clean reviewed checkpoint for the owner.

## Completed checkpoint

- 19 focused mapping/trajectory tests pass (8.31 seconds locally), including
  four five-interval native comparisons and six four-interval native recovery
  trajectories with controlled rejection. These establish state/initialization
  integration; they are not a new performance study or operating-point identity
  claim.
- The soft-terminal comparison exposed a pre-existing dependence of stepwise
  SoC reconstruction on CVXPY variable ordering. Reconstruction now follows
  chronological step order in the shared shift path.
- Full regression: 3,085 tests and six subtests passed, 58 warnings, 245.13 seconds.
  Ruff passes; mypy passes all seven configured sources including the new
  mapping module; whitespace checks pass.
- `cvxopf-review` independently passed all 19 new tests and returned CLEAN under
  scientific/public-API standards, including coordinate ordering, causality,
  initial boundaries, and duplicate-work review. No large study was launched.
- All changes remain unstaged for owner review; no commit or push was performed.
