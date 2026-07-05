# cvxopf

AC optimal power flow (AC-OPF) via CVXPY's disciplined nonlinear programming
(DNLP) framework, solved with IPOPT.

## Prerequisites — install these before `pip install cvxopf`

`cvxopf` requires the IPOPT nonlinear solver. The system library must be
installed first, then the Python interface `cyipopt`:

**Ubuntu / Debian**
```bash
sudo apt-get install coinor-libipopt-dev
pip install cyipopt
```

**macOS**
```bash
brew install ipopt
pip install cyipopt
```

**Windows** (conda recommended)
```bash
conda install -c conda-forge ipopt
pip install cyipopt
```

Full IPOPT installation documentation:
https://coin-or.github.io/Ipopt/INSTALL.html

## Installation

Once the prerequisites above are satisfied:

```bash
pip install -e ".[dev]"
```

## Overview

`cvxopf` formulates AC-OPF problems as smooth nonlinear programs using the
CVXPY DNLP interface (requires `cvxpy>=1.9`) and solves them with IPOPT. It
is designed to:

- Run MATPOWER/Pypower test cases out of the box
- Support single-shot optimization over multiple time steps
- Accept time-varying nodal load as pandas DataFrames
- Serve as a foundation for energy storage and battery models with
  state-of-charge dynamics (future work)

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

## Testing

```bash
pytest
```

Validation tests against Pypower reference data use committed fixture files
in `tests/fixtures/`. To regenerate those fixtures (requires `uv`):

```bash
uv run scripts/generate_pypower_fixtures.py
```

## Project structure

```
src/cvxopf/       Core package
tests/            Pytest test suite
tests/fixtures/   Committed Pypower reference outputs (static)
scripts/          Fixture generation script (uv-managed, isolated)
examples/         Runnable example scripts
```

## Milestones

- [x] Milestone 0: Repository skeleton
- [ ] Milestone 1: Port and modularize working code
- [ ] Milestone 2: Pypower fixture generation and validation tests
- [ ] Milestone 3: Multi-step problem builder
- [ ] Milestone 4: Branch flow limits
- [ ] Milestone 5: Battery/storage model hook
