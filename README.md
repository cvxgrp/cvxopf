# cvxopf

[![CI](https://github.com/cvxgrp/cvxopf/actions/workflows/ci.yml/badge.svg)](https://github.com/cvxgrp/cvxopf/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/cvxgrp/cvxopf/graph/badge.svg?token=f16a3ea1-bcfd-409e-8592-77d5bce001f5)](https://codecov.io/gh/cvxgrp/cvxopf)

Optimal power flow via CVXPY, supporting AC-OPF (nonconvex, DNLP) and
lossy DC OPF (convex QP).

## Overview

`cvxopf` formulates optimal power flow problems using CVXPY and solves them
with appropriate solvers. It is designed to:

- Run MATPOWER/Pypower test cases out of the box
- Support multiple OPF formulations from a single entry point
- Support single-shot optimization over multiple time steps
- Accept time-varying nodal load as pandas DataFrames
- Serve as a foundation for energy storage and battery models with
  state-of-charge dynamics (future work)

### Formulations

| Key | Description | Convex | Solver |
|---|---|---|---|
| `"ac"` | Full AC-OPF via CVXPY DNLP (requires `cvxpy>=1.9`) | No | IPOPT |
| `"lossy_dc"` | Lossy DC OPF (Boyd et al.) | Yes | CLARABEL |

Reference for lossy DC OPF: *Convex Optimization with Smart Grid Examples*,
https://doi.org/10.2172/3018252

## Prerequisites

`cvxopf` requires the IPOPT nonlinear solver system library. This must be
installed before running `pip install cvxopf`.

**Ubuntu / Debian**
```bash
sudo apt-get update
sudo apt-get install -y coinor-libipopt-dev liblapack-dev libblas-dev gfortran
```

> **Note:** On Linux, `coinor-libipopt-dev` alone is not sufficient.
> `liblapack-dev`, `libblas-dev`, and `gfortran` are also required because
> IPOPT's internal linear solver (MUMPS) links against them at build time.
> Without these, `pip install cvxopf` will fail when building `cyipopt`
> with a linker error (`cannot find -llapack`, `cannot find -lblas`).

**macOS**
```bash
brew install ipopt
```

**Windows** (conda recommended)
```bash
conda install -c conda-forge ipopt
```

Full IPOPT installation documentation:
https://coin-or.github.io/Ipopt/INSTALL.html

## Installation

Once the IPOPT system library is installed:

```bash
pip install git+https://github.com/cvxgrp/cvxopf.git
```

This will automatically install all Python dependencies including `cyipopt`
(the Python interface to IPOPT), `cvxpy`, `numpy`, and `pandas`.

When the package is published to PyPI, installation will simplify to:

```bash
pip install cvxopf  # coming soon
```

## Quick start

**AC-OPF:**

```python
from cvxopf.testcases import case9
from cvxopf.problem import build_opf
from cvxopf.results import extract_results

build = build_opf(case9(), formulation="ac")
build.solve()
results = extract_results(build)
print(f"Objective: {results['objective']:.2f} $/hr")
print(f"Pg (MW):   {results['Pg']}")
```

**Lossy DC OPF:**

```python
from cvxopf.testcases import case14
from cvxopf.problem import build_opf
from cvxopf.results import extract_results

build = build_opf(case14(), formulation="lossy_dc")
build.solve()
results = extract_results(build)
print(f"Objective:  {results['objective']:.2f} $/hr")
print(f"Pg (MW):    {results['Pg']}")
print(f"Flows (MW): {results['p_flows']}")
```

## Interactive notebooks

```bash
uv run --extra notebook marimo run notebooks/cvxopf_demo.py
```

Select a test case (case9 through case118), choose AC-OPF or lossy DC OPF,
adjust generator limits, branch flow limits, and load scale interactively.
Results update automatically after each solve.

```bash
uv run --extra notebook marimo run notebooks/benchmark_opf.py
```

Select number of repetitions and run timing study across all test cases and OPF configurations. The results should look something like this:

![OPF benchmark: AC sparse vs AC dense vs lossy DC](notebooks/benchmark_opf_result.png)

## Multi-step example

```python
import numpy as np
import pandas as pd
from cvxopf.testcases import case9
from cvxopf.problem import build_opf_multistep
from cvxopf.results import extract_results

ppc     = case9()
T       = 3
Pd_base = ppc["bus"][:, 2]
Qd_base = ppc["bus"][:, 3]

# Three time steps at 80%, 100%, 120% of base load
scales  = [0.8, 1.0, 1.2]
df_P    = pd.DataFrame(np.outer(scales, Pd_base))
df_Q    = pd.DataFrame(np.outer(scales, Qd_base))

build   = build_opf_multistep(ppc, df_P, df_Q, T=T, formulation="ac")
build.solve()
results = extract_results(build)
print(f"Total objective: {results['objective']:.2f} $/hr")
print(f"Pg per step (MW):\n{results['Pg']}")
```

## Project structure

```
src/cvxopf/           Core package
  problem.py          Public API: build_opf, build_opf_multistep
  ac_problem.py       AC-OPF helpers (DNLP formulation)
  dc_problem.py       Lossy DC OPF helpers (convex QP)
  network.py          Ybus, incidence matrices, reindexing
  cost.py             Generator cost expression builders
  data.py             Input validation and time-series handling
  results.py          Result extraction and comparison utilities
  testcases/          Built-in MATPOWER test cases (case9 — case118)
tests/                Pytest test suite
tests/fixtures/       Committed Pypower reference outputs (static)
scripts/              Fixture and test case generation scripts
notebooks/            Interactive marimo notebooks
examples/             Runnable example scripts
```

## Development

Clone the repository and install in editable mode with development dependencies:

```bash
git clone https://github.com/cvxgrp/cvxopf.git
cd cvxopf
```

If you have `uv` installed, that's it. Just run things with `uv` from the project root. 

If you are managing your own virtual environment, then install the development dependencies with pip:

```bash
pip install -e ".[dev]"
```

If you want to run the Marimo notebooks, you'll want the notebook dependencies as well:

```bash
pip install -e ".[dev,notebook]"
```

To run the test suite:

```bash
# If using uv (recommended)
uv run --extra dev pytest tests/ -v

# If installed directly with pip
pytest tests/ -v
```

To regenerate the Pypower reference fixtures (requires `uv`):

```bash
uv run scripts/generate_pypower_fixtures.py
```

Note: the fixture script runs in an isolated environment with pinned
`pypower==5.1.19` and `numpy==2.2.6`. It does not affect the main
package environment.

## Milestones

- [x] Milestone 0: Repository skeleton
- [x] Milestone 1: Port and modularize working code
- [x] Milestone 2: Pypower fixture generation and validation tests
- [x] Milestone 3: Multi-step problem builder
- [ ] Milestone 4: Branch flow limits (AC)
- [x] Milestone 5: Battery/storage model hook
- [x] Milestone 6: Lossy DC OPF and multi-formulation architecture
- [ ] Milestone 7: HVDC transmission links
- [ ] Milestone 8: Renewable generation (solar and wind)
- [x] Milestone 9: Sparse P/Q variables for AC-OPF