# cvxopf

[![CI](https://github.com/cvxgrp/cvxopf/actions/workflows/ci.yml/badge.svg)](https://github.com/cvxgrp/cvxopf/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/cvxgrp/cvxopf/graph/badge.svg?token=f16a3ea1-bcfd-409e-8592-77d5bce001f5)](https://codecov.io/gh/cvxgrp/cvxopf)

AC optimal power flow (AC-OPF) via CVXPY's disciplined nonlinear programming
(DNLP) framework, solved with IPOPT.

## Overview

`cvxopf` formulates AC-OPF problems as smooth nonlinear programs using the
CVXPY DNLP interface (requires `cvxpy>=1.9`) and solves them with IPOPT. It
is designed to:

- Run MATPOWER/Pypower test cases out of the box
- Support single-shot optimization over multiple time steps
- Accept time-varying nodal load as pandas DataFrames
- Serve as a foundation for energy storage and battery models with
  state-of-charge dynamics (future work)

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
pip install git+https://github.com/bmeyers/cvxopf.git
```

This will automatically install all Python dependencies including `cyipopt`
(the Python interface to IPOPT), `cvxpy`, `numpy`, and `pandas`.

When the package is published to PyPI, installation will simplify to:

```bash
pip install cvxopf  # coming soon
```

## Quick start

```python
import cvxpy as cp
from cvxopf.testcases import case9
from cvxopf.problem import build_acopf
from cvxopf.results import extract_results

build = build_acopf(case9())
build.prob.solve(solver=cp.IPOPT)
results = extract_results(build)
print(f"Objective: {results['objective']:.2f} $/hr")
print(f"Pg (MW):   {results['Pg']}")
```

## Multi-step example

```python
import numpy as np
import pandas as pd
import cvxpy as cp
from cvxopf.testcases import case9
from cvxopf.problem import build_acopf_multistep
from cvxopf.results import extract_results

ppc     = case9()
T       = 3
Pd_base = ppc["bus"][:, 2]
Qd_base = ppc["bus"][:, 3]

# Three time steps at 80%, 100%, 120% of base load
scales  = [0.8, 1.0, 1.2]
df_P    = pd.DataFrame(np.outer(scales, Pd_base))
df_Q    = pd.DataFrame(np.outer(scales, Qd_base))

build   = build_acopf_multistep(ppc, df_P, df_Q, T=T)
build.prob.solve(solver=cp.IPOPT)
results = extract_results(build)
print(f"Total objective: {results['objective']:.2f} $/hr")
print(f"Pg per step (MW):\n{results['Pg']}")
```

## Project structure

```
src/cvxopf/           Core package
  network.py          Ybus construction and bus/branch topology
  problem.py          Single-step and multi-step OPF problem builders
  cost.py             Generator cost expression builders
  data.py             Input validation and time-series handling
  results.py          Result extraction and comparison utilities
  testcases/          Built-in MATPOWER test cases (case9, case14)
tests/                Pytest test suite
tests/fixtures/       Committed Pypower reference outputs (static)
scripts/              Fixture generation script (uv-managed, isolated)
examples/             Runnable example scripts
```

## Development

Clone the repository and install in editable mode with development dependencies:

```bash
git clone https://github.com/cvxgrp/cvxopf.git
cd cvxopf
pip install -e ".[dev]"
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
- [ ] Milestone 4: Branch flow limits
- [ ] Milestone 5: Battery/storage model hook