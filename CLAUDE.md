# CLAUDE.md — Developer Guide for AI Coding Agents

This file provides context for AI coding agents working in the `cvxopf`
repository. Read this before making any changes.

---

## What this project is

`cvxopf` is a Python package for optimal power flow (OPF) using CVXPY,
supporting multiple formulations:

- **AC-OPF** via CVXPY's disciplined nonlinear programming (DNLP) framework,
  solved via IPOPT (nonconvex)
- **Lossy DC OPF** as a convex QP, solved via CLARABEL

It is designed for power systems research, with a focus on extensibility to
multi-step optimization and energy storage models.

The package is developed by the CVX Group at Stanford. The primary near-term
extension is a battery/storage model with state-of-charge dynamics
(Milestone 5), which will be provided by the researcher and integrated via
the coupling constraints hook in `build_opf_multistep`.

---

## Repository layout

```
src/cvxopf/
  __init__.py         Import-time cyipopt check with helpful error message
  network.py          Ybus, incidence matrices, reindexing
  cost.py             Polynomial generator cost expressions (CVXPY)
  data.py             Input validation, time-series DataFrame ingestion
  problem.py          Public API: OPFBuild, OPFOptions, build_opf,
                      build_opf_multistep, deprecated aliases
  ac_problem.py       AC-OPF internal helpers (DNLP formulation)
  dc_problem.py       Lossy DC OPF internal helpers (convex QP)
  results.py          extract_results, compare_to_reference
  testcases/
    case9.py          9-bus, 3-generator MATPOWER test case
    case14.py         IEEE 14-bus MATPOWER test case
tests/
  conftest.py
  fixtures/           Committed Pypower reference JSON files (static)
  test_network.py
  test_problem_single.py
  test_problem_multistep.py
  test_problem_dc.py
  test_problem_dc_multistep.py
  test_results.py
  test_vs_pypower_reference.py
scripts/
  generate_pypower_fixtures.py   uv inline-dependency script (isolated env)
examples/
  case9_single_step.py
  case14_single_step.py
  case9_multistep_flat_load.py
  case14_lossy_dc.py
```

---

## Running tests

Always use `uv run` so the correct virtual environment and extras are used:

```bash
uv run --extra dev pytest tests/ -v
```

Expected result: **244 passed, 0 failed, 0 skipped.**

To run a single test file:

```bash
uv run --extra dev pytest tests/test_network.py -v
```

To run with coverage:

```bash
uv run --extra dev pytest tests/ --cov=cvxopf --cov-branch --cov-report=term-missing
```

Do not use plain `pytest` without `uv run --extra dev` — it may use the
wrong environment and fail to find dependencies.

---

## Critical: how to solve OPF problems

**Always use the `build.solve()` convenience method. Never call
`build.prob.solve()` directly.**

`build.solve()` sets the correct solver defaults based on the formulation:

| `is_convex` | `formulation` | Solver default | `nlp` default |
|---|---|---|---|
| `False` | `"ac"` | `cp.IPOPT` | `True` |
| `True` | `"lossy_dc"` | `cp.CLARABEL` | `False` |

```python
build = build_opf(case9(), formulation="ac")
build.solve()                  # correct — IPOPT, nlp=True
build.solve(verbose=True)      # correct — shows solver output

build = build_opf(case9(), formulation="lossy_dc")
build.solve()                  # correct — CLARABEL, nlp=False
```

**Why `nlp=True` matters for AC:** AC-OPF problems are nonconvex and will
fail CVXPY's DCP check. `nlp=True` bypasses the DCP check and invokes DNLP
canonicalization instead. Calling `build.prob.solve(solver=cp.IPOPT)`
without `nlp=True` will raise a `DCPError`.

**Why `nlp=False` matters for DC:** Lossy DC OPF is a convex QP. Setting
`nlp=True` on a convex problem is incorrect and may produce wrong results.

---

## Formulations

### `"ac"` — Full AC-OPF (DNLP)

The formulation uses auxiliary `(nb, nb)` matrices `P` and `Q` to express
power flows via elementwise trig expressions on the Ybus sparsity pattern.
Nodal injections `p`, `q` are row sums of `P`, `Q`. Generator variables
`Pg`, `Qg` are linked via the incidence matrix `Cg`.

Variables: `theta`, `v`, `P`, `Q`, `p`, `q`, `Pg`, `Qg`

Results keys: `status`, `objective`, `Pg`, `Qg`, `Vm`, `Va_deg`,
`p_net`, `q_net`

Do not change this formulation without understanding the DNLP paper.

### `"lossy_dc"` — Lossy DC OPF (convex QP)

Reference: *Convex Optimization with Smart Grid Examples*,
https://doi.org/10.2172/3018252

Objective: minimize `G + loss_weight * L`
- `G = sum_k (c0_k + c1_k * Pg_k + c2_k * Pg_k^2)` — generation cost
- `L = sum_e r_e * p_flows_e^2` — line losses
- `loss_weight` is user-configurable via `OPFOptions.loss_weight` (default 1.0)

Constraints:
- `A @ p_flows + p_gen == Pd` — flow conservation at every bus
- `|p_flows[e]| <= f_max[e]` — branch flow limits
- Generator output bounds

Variables: `p_flows`, `p_gen`

Results keys: `status`, `objective`, `Pg`, `p_flows`, `p_net`

Note: `Vm`, `Va_deg`, `Qg`, `q_net` are **absent** from DC results.
Code consuming results from either formulation should use
`results.get('Vm')` rather than `results['Vm']`.

There is no Pypower oracle for DC validation. Correctness is verified via
internal consistency checks: flow conservation, bound feasibility, T=1
equivalence with single-step.

### Future formulations

The dispatch architecture in `problem.py` accepts new formulation keys
without API changes. Planned future formulations:

| Key | Description |
|---|---|
| `"fast_decoupled"` | Fast-decoupled AC (convex) |
| `"socp"` | SOCP relaxation (convex) |

To add a new formulation: implement `_build_<name>_single` and
`_build_<name>_multistep` in a new `src/cvxopf/<name>_problem.py`,
add them to `_get_single_builders()` and `_get_multistep_builders()`
in `problem.py`, and add `_extract_<name>_results` in `results.py`.

---

## Public API

### Entry points

```python
build_opf(case, *, formulation="ac", options=None) -> OPFBuild
build_opf_multistep(case, df_P, df_Q, *, T, formulation="ac",
                    options=None, coupling_constraints=None) -> OPFBuild
```

### Deprecated aliases (will be removed in a future release)

```python
build_acopf(...)              # use build_opf(..., formulation="ac")
build_acopf_multistep(...)    # use build_opf_multistep(..., formulation="ac")
```

Both emit `DeprecationWarning` when called.

### `OPFOptions` fields

| Field | Type | Default | Applies to |
|---|---|---|---|
| `enforce_vset` | bool | False | AC only |
| `sparsity_tol` | float | 0.0 | AC only |
| `init_flat` | bool | True | AC only |
| `enforce_branch_limits` | bool | False | AC only (stub) |
| `loss_weight` | float | 1.0 | DC only |
| `branch_limit_sentinel` | float | 1e6 | DC only |

### `OPFBuild` fields

| Field | Type | Description |
|---|---|---|
| `prob` | `cp.Problem` | The CVXPY problem |
| `variables` | dict | Named CVXPY variables |
| `data` | dict | Pre-computed numpy arrays and metadata |
| `formulation` | str | `"ac"` or `"lossy_dc"` |
| `is_convex` | bool | Drives solver defaults in `solve()` |

---

## Module responsibilities

`problem.py` is the **only** public-facing module. It imports from
`ac_problem.py` and `dc_problem.py` inside functions (not at module level)
to avoid circular imports. The import chain is:

```
problem.py  →  ac_problem.py  →  network.py, cost.py, data.py
problem.py  →  dc_problem.py  →  network.py, data.py
results.py  →  problem.py (OPFBuild type only)
```

`ac_problem.py` must not import from `dc_problem.py` and vice versa.

---

## Key design decisions

### Bus indexing
All internal computation uses 0-based consecutive bus indices.
`reindex_case_to_consecutive` in `network.py` handles remapping.
The `ext_to_int` mapping is stored in `OPFBuild.data`.
MATPOWER test cases use 1-based bus IDs; reindexing is always applied.

### Units
- Internal CVXPY variables are in **per-unit** (divided by `baseMVA`)
- `extract_results` scales back to **engineering units** (MW, MVAr, degrees)
- Generator cost expressions receive `Pg` in **MW** — the `baseMVA`
  scaling is applied before building cost expressions in both AC and DC

### Multi-step structure
`build_opf_multistep` builds a **single `cp.Problem`** containing T sets
of per-step variables and constraints. The objective is the sum of per-step
costs. Coupling constraints (e.g., battery SoC dynamics) are passed via
`coupling_constraints` and appended without modification.

### Incidence matrices
There are two distinct incidence matrices in `network.py`:

- `make_incidence_matrix(case)` — generator-to-bus matrix `Cg`, shape
  `(nb, ng)`. Used in both AC and DC to link generator variables to buses.
- `make_branch_node_incidence_matrix(case)` — branch-node matrix `A`,
  shape `(nb, nl)`. Used in DC for flow conservation `A @ p_flows + p_gen = Pd`.

Do not confuse them. See the module-level comment in `network.py`.

### Pypower is not a dependency
Never add pypower to `pyproject.toml`. See fixture generation below.

---

## Milestones

| Milestone | Status | Notes |
|---|---|---|
| 0 — Repository skeleton | ✅ Complete | |
| 1 — Port and modularize working code | ✅ Complete | |
| 2 — Pypower fixture generation and validation | ✅ Complete | |
| 3 — Multi-step problem builder | ✅ Complete | |
| 4 — Branch flow limits | 🔲 Stubbed | `OPFOptions.enforce_branch_limits=True` raises `NotImplementedError` in AC |
| 5 — Battery/storage model hook | 🔲 Architecture ready | `coupling_constraints` in `build_opf_multistep` |
| 6 — Lossy DC OPF and multi-formulation architecture | ✅ Complete | |
| 7 — HVDC transmission links | 🔲 Future | |
| 8 — Renewable generation | 🔲 Future | |

### Milestone 4 — Branch flow limits (AC)
When implementing, add apparent power flow expressions derived from the
`P`, `Q` matrices and enforce per-branch `rateA` constraints. The stub
and `NotImplementedError` in `ac_problem.py` must be replaced. Add tests
that verify the constraint is binding when load is pushed high enough.

### Milestone 5 — Battery/storage model hook
The researcher will provide example battery model code. The integration
point is the `coupling_constraints` parameter of `build_opf_multistep`.
Battery SoC dynamics constraints will reference per-step variables
(`Pg[t]`, `Qg[t]`, `v[t]` for AC; `p_gen[t]`, `p_flows[t]` for DC)
in `OPFBuild.variables`. Do not implement without researcher input.

### Milestone 7 — HVDC transmission links
Model HVDC links as controllable point-to-point power injections between
two buses, subject to capacity limits. Follows the MATPOWER `dcline`
table format (data format to be confirmed by researcher). Applies to both
AC and lossy DC formulations. Supports multi-step scheduling (the power
transfer on each DC link can vary per time step). Converter loss modeling
is deferred to implementation time.

Do not implement until the researcher provides the MATPOWER `dcline` data
format details.

### Milestone 8 — Renewable generation (solar and wind)
Model renewables as "can-take" generators: the available output at each
time step is given (from a PV or wind engineering model), the source can
be curtailed down to zero, and curtailment carries zero cost.

Key design points:
- Bus-connected, like conventional generators
- Zero cost: renewable generators contribute nothing to the objective
  regardless of output level. Do not add a curtailment penalty.
- Output bounded between 0 and available MW at each step
- Single-step: scalar available MW per renewable unit (or T=1 degenerate
  case of the time series interface — choose whichever is simpler for
  the user)
- Multi-step: time series of available MW as a pandas DataFrame, matching
  the load time series interface (one column per renewable unit, one row
  per time step)
- Data structure: to be determined by researcher. Likely bus-connected
  similar to the existing generator model.

Do not implement until the researcher provides the data structure
specification and example input data.

---

## Dependencies

### Runtime (installed with the package)
| Package | Constraint | Reason |
|---|---|---|
| `cvxpy` | `>=1.9` | DNLP interface (`cp.nlp.cos`, `cp.nlp.sin`) introduced in 1.9 |
| `numpy` | none | Array math, Ybus construction |
| `pandas` | none | Time-series load input |
| `cyipopt` | none | Python interface to IPOPT |

### System prerequisite (user must install manually)
IPOPT system library. Platform-specific instructions are in `README.md`.
On Linux, `liblapack-dev`, `libblas-dev`, and `gfortran` are also required
or `cyipopt` will fail to build with a linker error.

### Development extras
`pytest`, `pytest-cov` — installed via `pip install -e ".[dev]"` or
`uv run --extra dev`.

---

## Fixture generation

The Pypower reference fixtures in `tests/fixtures/` are static committed
files. They are **not** regenerated in CI. To regenerate them locally:

```bash
uv run scripts/generate_pypower_fixtures.py
```

This runs in an isolated sandbox with `pypower==5.1.19` and `numpy==2.2.6`.
The numpy pin is required because pypower uses `numpy.in1d` which was
removed in numpy 2.0. Do not run this script with the main package
environment.

Regenerate fixtures only if:
- A new test case is added to the package
- A suspected bug in an existing fixture needs to be ruled out

---

## Known acceptable discrepancies vs Pypower

case14 generator 3 (bus 6) and generator 4 (bus 8) Pg and Qg values may
appear as `~2e-9` in cvxopf where Pypower returns `0.00`. This is an
IPOPT interior-point solver artifact — the solver does not return exact
zeros at bounds. These are within the documented test tolerances and are
not bugs. They are noted in the `test_vs_pypower_reference.py` module
docstring.

---

## What not to do

- Do not add `pypower` to `pyproject.toml` or any runtime dependency
- Do not call `build.prob.solve()` directly — use `build.solve()`
- Do not use `build_acopf` or `build_acopf_multistep` — they are
  deprecated; use `build_opf(..., formulation="ac")` instead
- Do not change the DNLP variable formulation without understanding the paper
- Do not regenerate fixture files in CI
- Do not implement Milestone 5 without the researcher's battery model code
- Do not pin `numpy` in `pyproject.toml` — the numpy pin exists only in
  the fixture generation script
- Do not remove the `validate_case` call from `_parse_case` in
  `ac_problem.py` or `_parse_dc_case` in `dc_problem.py`
- Do not change units inside CVXPY expressions — keep everything in p.u.
  internally and scale only in `extract_results`
- Do not import `ac_problem` from `dc_problem` or vice versa
- Do not set `nlp=True` for convex formulations (DC, SOCP, fast-decoupled)
- Do not set `nlp=False` for the AC formulation