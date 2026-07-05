# CLAUDE.md — Developer Guide for AI Coding Agents

This file provides context for AI coding agents working in the `cvxopf`
repository. Read this before making any changes.

---

## What this project is

`cvxopf` is a Python package for AC optimal power flow (AC-OPF) using
CVXPY's disciplined nonlinear programming (DNLP) framework, solved via
IPOPT. It is designed for power systems research, with a focus on
extensibility to multi-step optimization and energy storage models.

The package is developed by the CVX Group at Stanford. The primary near-term
extension is a battery/storage model with state-of-charge dynamics
(Milestone 5), which will be provided by the researcher and integrated via
the coupling constraints hook in `build_acopf_multistep`.

---

## Repository layout

```
src/cvxopf/
  __init__.py         Import-time cyipopt check with helpful error message
  network.py          Ybus construction, reindexing, incidence matrix
  cost.py             Polynomial generator cost expressions (CVXPY)
  data.py             Input validation, time-series DataFrame ingestion
  problem.py          OPFBuild dataclass, build_acopf, build_acopf_multistep
  results.py          extract_results, compare_to_reference
  testcases/
    case9.py          9-bus, 3-generator MATPOWER test case
    case14.py         IEEE 14-bus MATPOWER test case
tests/
  conftest.py         Shared pytest fixtures
  fixtures/           Committed Pypower reference JSON files (static)
  test_network.py
  test_problem_single.py
  test_problem_multistep.py
  test_results.py
  test_vs_pypower_reference.py
scripts/
  generate_pypower_fixtures.py   uv inline-dependency script (isolated env)
examples/
  case9_single_step.py
  case14_single_step.py
  case9_multistep_flat_load.py
```

---

## Running tests

Always use `uv run` so the correct virtual environment and extras are used:

```bash
uv run --extra dev pytest tests/ -v
```

Expected result: **163 passed, 0 failed, 0 skipped.**

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

AC-OPF problems built by this package are **nonconvex**. They will fail
CVXPY's DCP check. The `nlp=True` argument is required to invoke DNLP
canonicalization instead.

**Always use the `build.solve()` convenience method:**

```python
build = build_acopf(case9())
build.solve()                    # correct
build.solve(verbose=True)        # correct, shows IPOPT output
```

**Never call `build.prob.solve()` directly in this codebase:**

```python
build.prob.solve(solver=cp.IPOPT)          # wrong — missing nlp=True
build.prob.solve(solver=cp.IPOPT, nlp=True) # technically correct but
                                            # bypasses our API
```

The `solve()` method on `OPFBuild` sets `solver=cp.IPOPT`, `nlp=True`,
and `verbose=False` as defaults. Any of these can be overridden by passing
keyword arguments:

```python
build.solve(verbose=True)         # show IPOPT output
build.solve(warm_start=True)      # use variable .value as initial point
```

This is the single most important invariant in the codebase. Any new test,
example, or documentation that calls `prob.solve()` directly is incorrect.

---

## Key design decisions

### Variable formulation
The DNLP paper formulation is used: auxiliary `(nb, nb)` matrices `P` and
`Q` express power flows via elementwise trig expressions on the Ybus
sparsity pattern. Nodal injections `p`, `q` are row sums of `P`, `Q`.
Generator variables `Pg`, `Qg` are linked via the incidence matrix `Cg`.
Do not change this formulation without understanding the DNLP paper.

### Bus indexing
All internal computation uses 0-based consecutive bus indices. The
`reindex_case_to_consecutive` function in `network.py` handles remapping.
The `ext_to_int` mapping is stored in `OPFBuild.data` for traceability.
MATPOWER test cases use 1-based bus IDs; reindexing is always applied.

### Units
- Internal CVXPY variables are in **per-unit** (divided by `baseMVA`)
- `extract_results` scales back to **engineering units** (MW, MVAr, degrees)
- `gencost` polynomial costs expect `Pg` in **MW** (not p.u.) — this
  scaling is applied inside `build_acopf` before passing to `poly_cost_expr`

### Multi-step structure
`build_acopf_multistep` builds a **single `cp.Problem`** containing T sets
of per-step variables and constraints. The objective is the sum of per-step
costs. Coupling constraints (e.g., battery SoC dynamics) are passed in via
the `coupling_constraints` parameter and appended without modification.

### Pypower is not a dependency
Pypower is used only to generate static reference fixture files. It is
managed in a completely isolated `uv` environment via inline script
dependencies. Never add pypower to `pyproject.toml`. See
`scripts/generate_pypower_fixtures.py`.

---

## Milestones

| Milestone | Status | Notes |
|---|---|---|
| 0 — Repository skeleton | ✅ Complete | |
| 1 — Port and modularize working code | ✅ Complete | |
| 2 — Pypower fixture generation and validation | ✅ Complete | |
| 3 — Multi-step problem builder | ✅ Complete | |
| 4 — Branch flow limits | 🔲 Stubbed | `OPFOptions.enforce_branch_limits=True` raises `NotImplementedError` |
| 5 — Battery/storage model hook | 🔲 Architecture ready | `coupling_constraints` parameter in `build_acopf_multistep` |

### Milestone 4 — Branch flow limits
When implementing, add apparent power flow expressions derived from the
`P`, `Q` matrices and enforce per-branch `rateA` constraints. The stub
and `NotImplementedError` in both `build_acopf` and `build_acopf_multistep`
must be replaced. Add tests that verify the constraint is binding when load
is pushed high enough.

### Milestone 5 — Battery/storage model hook
The researcher will provide example battery model code. The integration
point is the `coupling_constraints` parameter of `build_acopf_multistep`.
Battery SoC dynamics constraints will reference the per-step `Pg[t]`,
`Qg[t]`, and `v[t]` variables in `OPFBuild.variables`. Do not implement
this without the researcher's input.

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
- Do not change the DNLP variable formulation without understanding the paper
- Do not regenerate fixture files in CI
- Do not implement Milestone 5 without the researcher's battery model code
- Do not pin `numpy` in `pyproject.toml` — the numpy pin exists only in
  the fixture generation script
- Do not remove the `validate_case` call from `_parse_case` in `problem.py`
- Do not change units inside CVXPY expressions — keep everything in p.u.
  internally and scale only in `extract_results`
