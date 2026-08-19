# M17-S0 API and state-index characterization

## Outcome

The existing builders are sufficient for a manual hierarchical reference
runner once storage identity P1 is implemented. Storage dynamics and result
shapes are consistent across AC, lossy DC, and single-node DC, but two
contracts must not be inferred incorrectly:

1. reported `soc` contains post-step states only; and
2. the package does not currently expose a single accepted-primal predicate.

No controller or storage-identity implementation was added in S0.

## Storage state contract

For `T` multistep intervals and `ns` storage devices:

- `variables["b"]` and `variables["soc"]` are lists of `T` CVXPY variables,
  each with shape `(ns,)`;
- extracted `b` and `soc` have shape `(T, ns)`;
- `storage_initial_soc` has shape `(ns,)` and is not prepended to `soc`;
- `soc[t]` is the state after executing `b[t]`; and
- ideal-storage dynamics are

$$
e_{t+1} = e_t - \Delta t\,b_t.
$$

Consequently, construct the conceptual boundary trajectory as

```python
boundaries = np.vstack([build.data["storage_initial_soc"], results["soc"]])
```

Conceptual boundary `ell` maps to:

| Boundary | Current source |
|---:|---|
| `0` | `build.data["storage_initial_soc"]` |
| `ell >= 1` | `results["soc"][ell - 1]` |

This mapping is formulation-independent. The S0 test explicitly constructs a
three-interval frozen plan and a shortened two-interval replan. At controller
iteration `k=1`, replanned local boundary 2 is extracted from SoC result index
1 and represents global boundary 3.

Single-step and multistep `T=1` remain intentionally distinct:

- single-step storage variables and results have shape `(ns,)`; and
- multistep `T=1` variables remain one-element lists and results retain shape
  `(1, ns)`.

The characterization solves and extracts both modes under the same nontrivial
terminal equality, verifies their one-interval recurrence, and confirms the
terminal target and deviation in both result schemas.

The manual runner must not squeeze the time dimension or treat `soc[0]` as the
initial state.

## Terminal policy contract

Storage terminal constraints and costs are horizon hooks. They act on the last
post-step SoC variable exactly once:

- equality: `soc[-1] == terminal_soc`;
- shortfall floor: `soc[-1] >= terminal_soc`;
- two-sided or one-sided soft costs: one unscaled horizon-boundary term.

For M17, each outer plan is configured with the approved hard equality
`e_H = e_0`. A shortened plan created at global iteration `k` still ends at
the original global boundary `H`; its local result index for that boundary is
`H - k - 1`.

## Result and status contract

`OPFBuild.solve()` selects CLARABEL for convex formulations and IPOPT through
the DNLP path for AC. Solver exceptions propagate to the caller.

`extract_results()` initializes a schema from the build and then extracts
fields independently. Relevant observed behavior is:

- before solve, `status is None`, objective and scalar costs are NaN, and
  storage primals are `None`;
- known exogenous load inputs remain available without a primal solution;
- unavailable or incomplete storage histories produce `b is None` or
  `soc is None` rather than a partial stacked history;
- AC branch diagnostics require available voltage and angle values; and
- raw CVXPY status alone is not a complete accepted-action contract.

There is no shared package helper that answers whether a build has a usable
controlling primal. The Phase-1 runner therefore needs one explicit local
predicate, later promoted or replaced by the typed public M17 contract.

Minimum finite fields are role-specific:

| Solve role | Required controlling fields |
|---|---|
| Outer `lossy_dc` | `b`, `soc`, `Pg`, `p_net`, `p_flows`, fixed load input/service, applicable device outputs, and DC diagnostics used by the frozen residual checks |
| Inner AC | `b`, `soc`, `b_q`, `Pg`, `Qg`, `Vm`, `Va_deg`, `p_net`, `q_net`, all branch-terminal real/reactive/apparent flows, fixed load input/service, and every participating device output |
| Both | finite first action and first post-step SoC, matching explicit storage IDs, finite objective/status metadata, and policy-specific terminal deviation or cost quantities |

Conditional components add their complete result channels: ND active output,
curtailment, and AC reactive output; both HVDC terminal injections and loss;
and, in later shedding studies, served/shed active and reactive load, fraction,
ENS, and shedding cost. Missing required network or component diagnostics make
the result nonexecutable even when battery fields are available.

Approved baseline acceptance rule:

1. raw status is `optimal` or `optimal_inaccurate`;
2. every field required to execute and audit the first interval is present and
   finite;
3. storage recurrence, power balance, voltage, and both-terminal thermal
   residuals satisfy their frozen tolerances; and
4. the hard or soft terminal condition satisfies its policy-specific check.

`user_limit`, solver exceptions, missing fields, and nonfinite fields are not
accepted for execution. `optimal_inaccurate` remains eligible only after the
same explicit residual checks; its raw status is retained.

## Storage identity finding

`StorageUnitIdeal` has no `device_id`, and storage metadata contains no aligned
identity vector. The current model is ordered only by the input sequence. This
is acceptable inside one build but cannot support audited cross-build state
handoff.

P1 must therefore:

- append `device_id: str | None = None` to preserve positional constructors;
- validate every supplied ID as a nonempty string and reject duplicates;
- publish aligned storage IDs in `OPFBuild.data` and results;
- retain build-local positional labels for legacy builds without claiming
  cross-build identity;
- preserve explicit IDs through `dataclasses.replace` when constructing
  shortened outer plans and AC windows; and
- require explicit, unique, exactly matching storage-ID sets at the M17
  boundary, aligning by ID and rejecting missing or extra devices.

The P1 slice should land before scenario freeze and before the manual runner.

## Characterization evidence

`tests/test_m17_characterization.py` freezes:

- all three formulations;
- single-step versus intentional multistep `T=1` schemas;
- solved single-step and multistep `T=1` result shapes, recurrence, and
  terminal-policy behavior;
- three-step post-step SoC dynamics and global terminal equality;
- shortened-plan local-boundary mapping; and
- unsolved result/status behavior.

Verification at the S0 stopping point:

- 10 focused M17 characterization tests passed;
- the complete suite passed with 1,637 tests;
- Ruff, configured strict mypy, and `git diff --check` passed; and
- no production implementation changed.
