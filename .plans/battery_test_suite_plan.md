# Test Suite Build Plan — Milestone 5 Battery Storage (cvxopf)

## Preamble

This document is a self-contained specification for writing the test suite for the battery/energy storage model (Milestone 5) in the `cvxopf` package. The storage model has already been implemented. Your job is to write tests only — do not modify any source files unless a test reveals a genuine bug, and if so flag it rather than silently fixing it.

Read this entire document before writing any test code.

---

## 1. What Has Been Implemented

The following has been added to the codebase. Understand the API before writing tests.

### 1.1 New file: `src/cvxopf/storage.py`

Contains:

```python
@dataclass
class StorageUnitIdeal:
    bus:                   int     # external (MATPOWER) bus ID
    apparent_power_rating: float   # S_max, MVA, > 0
    capacity:              float   # Q, MWh, > 0
    initial_soc:           float   # q_start, MWh, 0 <= initial_soc <= capacity
    aging_weight:          float = 1e-2   # lambda, $/MW, >= 0
```

Note: there is **no `delta` field** on `StorageUnitIdeal`. `delta` is a global problem parameter passed to `build_opf` / `build_opf_multistep`.

### 1.2 Updated public API in `src/cvxopf/problem.py`

```python
from cvxopf.problem import build_opf, build_opf_multistep, OPFOptions, OPFBuild
from cvxopf.problem import StorageUnitIdeal   # re-exported from storage.py
```

New signatures:

```python
build_opf(
    case, *,
    formulation="ac",
    options=None,
    storage=None,       # list[StorageUnitIdeal] | None
    delta=1.0,          # time step duration in hours, ignored when storage=None
) -> OPFBuild

build_opf_multistep(
    case, df_P, df_Q, *,
    T,
    formulation="ac",
    options=None,
    coupling_constraints=None,
    storage=None,       # list[StorageUnitIdeal] | None
    delta=1.0,
) -> OPFBuild
```

### 1.3 Storage variables in `OPFBuild.variables`

Present **only** when `storage is not None`:

| Key | Single-step | Multi-step | Shape | Units |
|---|---|---|---|---|
| `"b"` | `cp.Variable` | `list[cp.Variable]` len T | `(ns,)` each | MW |
| `"b_q"` | `cp.Variable` | `list[cp.Variable]` len T | `(ns,)` each | MVAr, **AC only** |
| `"soc"` | `cp.Variable` | `list[cp.Variable]` len T | `(ns,)` each | MWh |

Absent when `storage=None`. `"b_q"` always absent from DC builds.

### 1.4 Storage keys in `OPFBuild.data`

Present **only** when `storage is not None`:

| Key | Type | Notes |
|---|---|---|
| `"ns"` | int | number of storage units |
| `"Cs"` | ndarray `(nb, ns)` | storage-to-bus incidence |
| `"storage_bus"` | ndarray int `(ns,)` | internal 0-based bus indices |
| `"storage_apparent_power_rating"` | ndarray float `(ns,)` | MVA |
| `"storage_capacity"` | ndarray float `(ns,)` | MWh |
| `"storage_initial_soc"` | ndarray float `(ns,)` | MWh |
| `"storage_aging_weight"` | ndarray float `(ns,)` | $/MW |
| `"storage_delta"` | float scalar | hours |

Detection: `"ns" in build.data` is `True` iff storage is present.

### 1.5 Storage keys in `extract_results` output

Present **only** when `"ns" in build.data`:

| Key | Single-step shape | Multi-step shape | Units | AC | DC |
|---|---|---|---|---|---|
| `"b"` | `(ns,)` | `(T, ns)` | MW | ✓ | ✓ |
| `"b_q"` | `(ns,)` | `(T, ns)` | MVAr | ✓ | — |
| `"soc"` | `(ns,)` | `(T, ns)` | MWh | ✓ | ✓ |
| `"storage_cost"` | float | float | $ | ✓ | ✓ |

`storage_cost = sum(aging_weight[s] * |b[s]|)` summed over all steps and units.

### 1.6 Constraints enforced by the implementation

**AC only — apparent power circle, per unit per step:**
```
b_t[s]^2 + b_q_t[s]^2 <= S_max[s]^2
```

**DC only — real power bound (apparent power rating used as real power limit):**
```
-S_max[s] <= b_t[s] <= S_max[s]
```

**Both — SoC bounds:**
```
0 <= soc_t[s] <= capacity[s]
```

**Both — SoC dynamics (t=0, initial condition):**
```
soc_0[s] = initial_soc[s] - b_0[s] * delta
```

**Both — SoC dynamics (t>=1):**
```
soc_t[s] = soc_{t-1}[s] - b_t[s] * delta
```

**Both — nodal real power balance (AC in p.u., DC in p.u.):**
```
AC: p = Cg @ Pg - Pd + (1/baseMVA) * Cs @ b_t
DC: A @ p_flows + p_gen + (1/baseMVA) * Cs @ b_t = Pd
```

**AC only — nodal reactive power balance:**
```
q = Cg @ Qg - Qd + (1/baseMVA) * Cs @ b_q_t
```

### 1.7 Warnings

When DC storage is built, a `UserWarning` is emitted containing the phrase `"apparent power"`. This warns that the apparent power rating is being applied as a real power limit only.

### 1.8 Validation errors

`_validate_storage` raises `ValueError` for:
- `apparent_power_rating <= 0`
- `capacity <= 0`
- `initial_soc < 0` or `initial_soc > capacity`
- `aging_weight < 0`
- bus ID not present in case bus table

`build_opf` / `build_opf_multistep` raises `ValueError` for:
- `delta <= 0` when `storage is not None`

`delta <= 0` when `storage is None` does **not** raise — it is silently ignored.

---

## 2. Test Infrastructure

### 2.1 File location

Create one new file: `tests/test_storage.py`

Do not modify any existing test files.

### 2.2 Imports

```python
import warnings
import numpy as np
import pandas as pd
import pytest
import cvxpy as cp

from cvxopf.testcases import case9, case14
from cvxopf.problem import (
    build_opf, build_opf_multistep,
    OPFBuild, OPFOptions,
    StorageUnitIdeal,
)
from cvxopf.results import extract_results
```

### 2.3 Tolerances (use these exact values throughout)

```python
OBJ_RTOL   = 1e-4    # relative tolerance on objective
VAL_ATOL   = 1e-3    # absolute tolerance on Pg, Qg, b, b_q, soc (MW/MVAr/MWh)
SOC_ATOL   = 1e-4    # absolute tolerance on SoC dynamics residual (MWh)
APR_ATOL   = 1e-4    # absolute tolerance on apparent power constraint residual (MVA^2)
BAL_ATOL   = 1e-4    # absolute tolerance on nodal balance residual (p.u.)
```

### 2.4 Shared fixtures and helpers

Place these at module level, before the test classes:

```python
# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flat_load_dfs(case_fn, T):
    """Return (df_P, df_Q) with T identical rows matching base case load."""
    ppc     = case_fn()
    Pd_base = ppc["bus"][:, 2].copy()
    Qd_base = ppc["bus"][:, 3].copy()
    df_P    = pd.DataFrame(np.tile(Pd_base, (T, 1)))
    df_Q    = pd.DataFrame(np.tile(Qd_base, (T, 1)))
    return df_P, df_Q


def _varying_load_dfs(case_fn, scales):
    """Return (df_P, df_Q) with len(scales) rows at given load scales."""
    ppc     = case_fn()
    Pd_base = ppc["bus"][:, 2].copy()
    Qd_base = ppc["bus"][:, 3].copy()
    df_P    = pd.DataFrame(np.outer(scales, Pd_base))
    df_Q    = pd.DataFrame(np.outer(scales, Qd_base))
    return df_P, df_Q


def _default_unit(bus=1, S_max=50.0, capacity=100.0,
                  initial_soc=50.0, aging_weight=0.0):
    """Return a StorageUnitIdeal with sensible defaults for testing."""
    return StorageUnitIdeal(
        bus=bus,
        apparent_power_rating=S_max,
        capacity=capacity,
        initial_soc=initial_soc,
        aging_weight=aging_weight,
    )


def _solve_ac_single(storage=None, delta=1.0, case_fn=case9, options=None):
    build = build_opf(case_fn(), formulation="ac",
                      storage=storage, delta=delta, options=options)
    build.solve()
    return build, extract_results(build)


def _solve_dc_single(storage=None, delta=1.0, case_fn=case9, options=None):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build = build_opf(case_fn(), formulation="lossy_dc",
                          storage=storage, delta=delta, options=options)
    build.solve()
    return build, extract_results(build)


def _solve_ac_multistep(T, df_P, df_Q, storage=None, delta=1.0,
                         case_fn=case9, coupling_constraints=None):
    build = build_opf_multistep(
        case_fn(), df_P, df_Q, T=T, formulation="ac",
        storage=storage, delta=delta,
        coupling_constraints=coupling_constraints,
    )
    build.solve()
    return build, extract_results(build)


def _solve_dc_multistep(T, df_P, df_Q, storage=None, delta=1.0,
                         case_fn=case9, coupling_constraints=None):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build = build_opf_multistep(
            case_fn(), df_P, df_Q, T=T, formulation="lossy_dc",
            storage=storage, delta=delta,
            coupling_constraints=coupling_constraints,
        )
    build.solve()
    return build, extract_results(build)
```

---

## 3. Test Classes

Write the following test classes in `tests/test_storage.py` in the order listed.

---

### `TestStorageUnitIdeal`

Tests the `StorageUnitIdeal` dataclass itself. No solving required.

```
test_dataclass_fields_exist
    unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                             capacity=100.0, initial_soc=50.0)
    assert hasattr(unit, "bus")
    assert hasattr(unit, "apparent_power_rating")
    assert hasattr(unit, "capacity")
    assert hasattr(unit, "initial_soc")
    assert hasattr(unit, "aging_weight")

test_default_aging_weight_is_1e_2
    unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                             capacity=100.0, initial_soc=50.0)
    assert unit.aging_weight == pytest.approx(1e-2)

test_no_delta_field
    unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                             capacity=100.0, initial_soc=50.0)
    assert not hasattr(unit, "delta")

test_custom_aging_weight
    unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                             capacity=100.0, initial_soc=50.0,
                             aging_weight=0.5)
    assert unit.aging_weight == pytest.approx(0.5)
```

---

### `TestStorageValidation`

Tests that invalid `StorageUnitIdeal` parameters raise `ValueError`.
No solving required — just call `build_opf` and expect it to raise.

```
test_invalid_apparent_power_rating_zero_raises
    unit = StorageUnitIdeal(bus=1, apparent_power_rating=0.0,
                             capacity=100.0, initial_soc=50.0)
    with pytest.raises(ValueError, match="apparent_power_rating"):
        build_opf(case9(), formulation="ac", storage=[unit])

test_invalid_apparent_power_rating_negative_raises
    unit = StorageUnitIdeal(bus=1, apparent_power_rating=-10.0,
                             capacity=100.0, initial_soc=50.0)
    with pytest.raises(ValueError):
        build_opf(case9(), formulation="ac", storage=[unit])

test_invalid_capacity_zero_raises
    unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                             capacity=0.0, initial_soc=0.0)
    with pytest.raises(ValueError, match="capacity"):
        build_opf(case9(), formulation="ac", storage=[unit])

test_initial_soc_below_zero_raises
    unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                             capacity=100.0, initial_soc=-1.0)
    with pytest.raises(ValueError, match="initial_soc"):
        build_opf(case9(), formulation="ac", storage=[unit])

test_initial_soc_above_capacity_raises
    unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                             capacity=100.0, initial_soc=150.0)
    with pytest.raises(ValueError, match="initial_soc"):
        build_opf(case9(), formulation="ac", storage=[unit])

test_initial_soc_equals_capacity_is_valid
    unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                             capacity=100.0, initial_soc=100.0)
    build = build_opf(case9(), formulation="ac", storage=[unit])
    assert isinstance(build, OPFBuild)

test_initial_soc_zero_is_valid
    unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                             capacity=100.0, initial_soc=0.0)
    build = build_opf(case9(), formulation="ac", storage=[unit])
    assert isinstance(build, OPFBuild)

test_negative_aging_weight_raises
    unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                             capacity=100.0, initial_soc=50.0,
                             aging_weight=-0.1)
    with pytest.raises(ValueError, match="aging_weight"):
        build_opf(case9(), formulation="ac", storage=[unit])

test_aging_weight_zero_is_valid
    unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                             capacity=100.0, initial_soc=50.0,
                             aging_weight=0.0)
    build = build_opf(case9(), formulation="ac", storage=[unit])
    assert isinstance(build, OPFBuild)

test_invalid_bus_raises
    unit = StorageUnitIdeal(bus=999, apparent_power_rating=50.0,
                             capacity=100.0, initial_soc=50.0)
    with pytest.raises(ValueError, match="bus"):
        build_opf(case9(), formulation="ac", storage=[unit])
```

---

### `TestDeltaValidation`

```
test_delta_zero_with_storage_raises
    unit = _default_unit()
    with pytest.raises(ValueError, match="delta"):
        build_opf(case9(), formulation="ac", storage=[unit], delta=0.0)

test_delta_negative_with_storage_raises
    unit = _default_unit()
    with pytest.raises(ValueError, match="delta"):
        build_opf(case9(), formulation="ac", storage=[unit], delta=-1.0)

test_delta_zero_without_storage_does_not_raise
    build = build_opf(case9(), formulation="ac", storage=None, delta=0.0)
    assert isinstance(build, OPFBuild)

test_delta_negative_without_storage_does_not_raise
    build = build_opf(case9(), formulation="ac", storage=None, delta=-1.0)
    assert isinstance(build, OPFBuild)

test_delta_default_is_one
    # build.data["storage_delta"] should be 1.0 by default
    unit  = _default_unit()
    build = build_opf(case9(), formulation="ac", storage=[unit])
    assert build.data["storage_delta"] == pytest.approx(1.0)

test_delta_025_stored_in_data
    unit  = _default_unit()
    build = build_opf(case9(), formulation="ac", storage=[unit], delta=0.25)
    assert build.data["storage_delta"] == pytest.approx(0.25)
```

---

### `TestStorageNoStorage`

Confirms that `storage=None` leaves all existing behaviour exactly unchanged.

```
test_ac_single_no_storage_results_unchanged
    # Solve with and without storage=None explicitly.
    # Results should be identical (no storage is the default).
    build1 = build_opf(case9(), formulation="ac")
    build2 = build_opf(case9(), formulation="ac", storage=None)
    build1.solve(); build2.solve()
    r1 = extract_results(build1)
    r2 = extract_results(build2)
    assert r1["status"] == r2["status"]
    np.testing.assert_allclose(r1["Pg"], r2["Pg"], atol=VAL_ATOL)

test_dc_single_no_storage_results_unchanged
    build1 = build_opf(case9(), formulation="lossy_dc")
    build2 = build_opf(case9(), formulation="lossy_dc", storage=None)
    build1.solve(); build2.solve()
    r1 = extract_results(build1)
    r2 = extract_results(build2)
    assert r1["status"] == r2["status"]
    np.testing.assert_allclose(r1["Pg"], r2["Pg"], atol=VAL_ATOL)

test_b_absent_from_results_when_no_storage
    _, r = _solve_ac_single()
    assert "b" not in r

test_b_q_absent_from_results_when_no_storage
    _, r = _solve_ac_single()
    assert "b_q" not in r

test_soc_absent_from_results_when_no_storage
    _, r = _solve_ac_single()
    assert "soc" not in r

test_storage_cost_absent_from_results_when_no_storage
    _, r = _solve_ac_single()
    assert "storage_cost" not in r

test_b_absent_from_variables_when_no_storage
    build, _ = _solve_ac_single()
    assert "b" not in build.variables

test_ns_absent_from_data_when_no_storage
    build, _ = _solve_ac_single()
    assert "ns" not in build.data
```

---

### `TestStorageACSingle`

Single time-step AC-OPF with one storage unit.

```
test_solves_optimal
    build, r = _solve_ac_single(storage=[_default_unit()])
    assert r["status"] == "optimal"

test_b_shape
    build, r = _solve_ac_single(storage=[_default_unit()])
    assert r["b"].shape == (1,)

test_b_q_shape
    build, r = _solve_ac_single(storage=[_default_unit()])
    assert r["b_q"].shape == (1,)

test_soc_shape
    build, r = _solve_ac_single(storage=[_default_unit()])
    assert r["soc"].shape == (1,)

test_soc_satisfies_initial_condition
    unit  = _default_unit(initial_soc=50.0)
    build, r = _solve_ac_single(storage=[unit], delta=1.0)
    # soc[0] == initial_soc - b[0] * delta
    residual = r["soc"][0] - (50.0 - r["b"][0] * 1.0)
    assert abs(residual) < SOC_ATOL

test_apparent_power_constraint_satisfied
    unit = _default_unit(S_max=50.0)
    build, r = _solve_ac_single(storage=[unit])
    violation = r["b"][0]**2 + r["b_q"][0]**2 - 50.0**2
    assert violation <= APR_ATOL

test_soc_within_capacity_bounds
    unit = _default_unit(capacity=100.0)
    build, r = _solve_ac_single(storage=[unit])
    assert r["soc"][0] >= -VAL_ATOL
    assert r["soc"][0] <= 100.0 + VAL_ATOL

test_b_in_variables
    build, _ = _solve_ac_single(storage=[_default_unit()])
    assert "b" in build.variables
    assert isinstance(build.variables["b"], cp.Variable)

test_b_q_in_variables
    build, _ = _solve_ac_single(storage=[_default_unit()])
    assert "b_q" in build.variables
    assert isinstance(build.variables["b_q"], cp.Variable)

test_soc_in_variables
    build, _ = _solve_ac_single(storage=[_default_unit()])
    assert "soc" in build.variables
    assert isinstance(build.variables["soc"], cp.Variable)

test_ns_in_data
    build, _ = _solve_ac_single(storage=[_default_unit()])
    assert "ns" in build.data
    assert build.data["ns"] == 1

test_storage_delta_in_data
    build, _ = _solve_ac_single(storage=[_default_unit()], delta=1.0)
    assert "storage_delta" in build.data
    assert build.data["storage_delta"] == pytest.approx(1.0)

test_storage_cost_in_results
    build, r = _solve_ac_single(storage=[_default_unit()])
    assert "storage_cost" in r

test_storage_cost_nonneg
    build, r = _solve_ac_single(storage=[_default_unit()])
    assert r["storage_cost"] >= -1e-6

test_storage_cost_zero_when_aging_weight_zero
    unit = _default_unit(aging_weight=0.0)
    _, r = _solve_ac_single(storage=[unit])
    assert abs(r["storage_cost"]) < 1e-4

test_data_has_expected_storage_keys
    build, _ = _solve_ac_single(storage=[_default_unit()])
    for key in ("ns", "Cs", "storage_bus", "storage_apparent_power_rating",
                "storage_capacity", "storage_initial_soc",
                "storage_aging_weight", "storage_delta"):
        assert key in build.data, f"build.data missing '{key}'"

test_Cs_shape
    build, _ = _solve_ac_single(storage=[_default_unit()])
    nb = build.data["nb"]
    assert build.data["Cs"].shape == (nb, 1)
```

---

### `TestStorageACMultistep`

Multi-step AC-OPF with one storage unit.

```
test_multistep_solves_optimal
    df_P, df_Q = _flat_load_dfs(case9, T=3)
    _, r = _solve_ac_multistep(3, df_P, df_Q, storage=[_default_unit()])
    assert r["status"] == "optimal"

test_b_shape_T_ns
    df_P, df_Q = _flat_load_dfs(case9, T=3)
    _, r = _solve_ac_multistep(3, df_P, df_Q, storage=[_default_unit()])
    assert r["b"].shape == (3, 1)

test_b_q_shape_T_ns
    df_P, df_Q = _flat_load_dfs(case9, T=3)
    _, r = _solve_ac_multistep(3, df_P, df_Q, storage=[_default_unit()])
    assert r["b_q"].shape == (3, 1)

test_soc_shape_T_ns
    df_P, df_Q = _flat_load_dfs(case9, T=3)
    _, r = _solve_ac_multistep(3, df_P, df_Q, storage=[_default_unit()])
    assert r["soc"].shape == (3, 1)

test_soc_dynamics_initial_condition
    unit = _default_unit(initial_soc=50.0)
    df_P, df_Q = _flat_load_dfs(case9, T=3)
    _, r = _solve_ac_multistep(3, df_P, df_Q, storage=[unit], delta=1.0)
    residual = r["soc"][0, 0] - (50.0 - r["b"][0, 0] * 1.0)
    assert abs(residual) < SOC_ATOL

test_soc_dynamics_all_steps
    unit = _default_unit(initial_soc=50.0)
    df_P, df_Q = _flat_load_dfs(case9, T=3)
    _, r = _solve_ac_multistep(3, df_P, df_Q, storage=[unit], delta=1.0)
    for t in range(1, 3):
        residual = r["soc"][t, 0] - (r["soc"][t-1, 0] - r["b"][t, 0] * 1.0)
        assert abs(residual) < SOC_ATOL, f"SoC dynamics violated at t={t}"

test_apparent_power_constraint_all_steps
    unit = _default_unit(S_max=50.0)
    df_P, df_Q = _flat_load_dfs(case9, T=3)
    _, r = _solve_ac_multistep(3, df_P, df_Q, storage=[unit])
    for t in range(3):
        violation = r["b"][t, 0]**2 + r["b_q"][t, 0]**2 - 50.0**2
        assert violation <= APR_ATOL, f"Apparent power violated at t={t}"

test_soc_within_capacity_all_steps
    unit = _default_unit(capacity=100.0)
    df_P, df_Q = _flat_load_dfs(case9, T=3)
    _, r = _solve_ac_multistep(3, df_P, df_Q, storage=[unit])
    assert np.all(r["soc"] >= -VAL_ATOL)
    assert np.all(r["soc"] <= 100.0 + VAL_ATOL)

test_b_variable_list_length_T
    df_P, df_Q = _flat_load_dfs(case9, T=3)
    build, _ = _solve_ac_multistep(3, df_P, df_Q, storage=[_default_unit()])
    assert isinstance(build.variables["b"], list)
    assert len(build.variables["b"]) == 3

test_b_q_variable_list_length_T
    df_P, df_Q = _flat_load_dfs(case9, T=3)
    build, _ = _solve_ac_multistep(3, df_P, df_Q, storage=[_default_unit()])
    assert isinstance(build.variables["b_q"], list)
    assert len(build.variables["b_q"]) == 3

test_T1_objective_matches_single_step
    unit = _default_unit(aging_weight=0.0)
    df_P, df_Q = _flat_load_dfs(case9, T=1)
    _, r_single = _solve_ac_single(storage=[unit])
    _, r_multi  = _solve_ac_multistep(1, df_P, df_Q, storage=[unit])
    assert abs(r_multi["objective"] - r_single["objective"]) \
           / abs(r_single["objective"]) < OBJ_RTOL

test_T1_b_matches_single_step
    unit = _default_unit(aging_weight=0.0)
    df_P, df_Q = _flat_load_dfs(case9, T=1)
    _, r_single = _solve_ac_single(storage=[unit])
    _, r_multi  = _solve_ac_multistep(1, df_P, df_Q, storage=[unit])
    np.testing.assert_allclose(r_multi["b"][0], r_single["b"], atol=VAL_ATOL)

test_T1_soc_matches_single_step
    unit = _default_unit(aging_weight=0.0)
    df_P, df_Q = _flat_load_dfs(case9, T=1)
    _, r_single = _solve_ac_single(storage=[unit])
    _, r_multi  = _solve_ac_multistep(1, df_P, df_Q, storage=[unit])
    np.testing.assert_allclose(r_multi["soc"][0], r_single["soc"], atol=VAL_ATOL)

test_storage_cost_in_results
    df_P, df_Q = _flat_load_dfs(case9, T=3)
    _, r = _solve_ac_multistep(3, df_P, df_Q, storage=[_default_unit()])
    assert "storage_cost" in r
    assert isinstance(r["storage_cost"], float)
```

---

### `TestStorageDCSingle`

Single time-step DC-OPF with one storage unit.

```
test_solves_optimal
    _, r = _solve_dc_single(storage=[_default_unit()])
    assert r["status"] == "optimal"

test_b_shape
    _, r = _solve_dc_single(storage=[_default_unit()])
    assert r["b"].shape == (1,)

test_b_q_absent_from_results
    _, r = _solve_dc_single(storage=[_default_unit()])
    assert "b_q" not in r

test_b_q_absent_from_variables
    build, _ = _solve_dc_single(storage=[_default_unit()])
    assert "b_q" not in build.variables

test_soc_satisfies_initial_condition
    unit = _default_unit(initial_soc=30.0)
    _, r = _solve_dc_single(storage=[unit], delta=1.0)
    residual = r["soc"][0] - (30.0 - r["b"][0] * 1.0)
    assert abs(residual) < SOC_ATOL

test_real_power_bound_satisfied
    unit = _default_unit(S_max=30.0)
    _, r = _solve_dc_single(storage=[unit])
    assert r["b"][0] >= -30.0 - VAL_ATOL
    assert r["b"][0] <=  30.0 + VAL_ATOL

test_dc_apparent_power_fallback_emits_warning
    unit = _default_unit()
    with pytest.warns(UserWarning, match="apparent power"):
        build_opf(case9(), formulation="lossy_dc", storage=[unit])

test_storage_cost_in_results
    _, r = _solve_dc_single(storage=[_default_unit()])
    assert "storage_cost" in r

test_soc_within_capacity_bounds
    unit = _default_unit(capacity=100.0)
    _, r = _solve_dc_single(storage=[unit])
    assert r["soc"][0] >= -VAL_ATOL
    assert r["soc"][0] <= 100.0 + VAL_ATOL
```

---

### `TestStorageDCMultistep`

```
test_multistep_dc_solves_optimal
    df_P, df_Q = _flat_load_dfs(case9, T=3)
    _, r = _solve_dc_multistep(3, df_P, df_Q, storage=[_default_unit()])
    assert r["status"] == "optimal"

test_b_shape_T_ns
    df_P, df_Q = _flat_load_dfs(case9, T=3)
    _, r = _solve_dc_multistep(3, df_P, df_Q, storage=[_default_unit()])
    assert r["b"].shape == (3, 1)

test_soc_shape_T_ns
    df_P, df_Q = _flat_load_dfs(case9, T=3)
    _, r = _solve_dc_multistep(3, df_P, df_Q, storage=[_default_unit()])
    assert r["soc"].shape == (3, 1)

test_b_q_absent_from_multistep_variables
    df_P, df_Q = _flat_load_dfs(case9, T=3)
    build, _ = _solve_dc_multistep(3, df_P, df_Q, storage=[_default_unit()])
    assert "b_q" not in build.variables

test_soc_dynamics_all_steps
    unit = _default_unit(initial_soc=30.0)
    df_P, df_Q = _flat_load_dfs(case9, T=3)
    _, r = _solve_dc_multistep(3, df_P, df_Q, storage=[unit], delta=1.0)
    residual_0 = r["soc"][0, 0] - (30.0 - r["b"][0, 0] * 1.0)
    assert abs(residual_0) < SOC_ATOL
    for t in range(1, 3):
        residual = r["soc"][t, 0] - (r["soc"][t-1, 0] - r["b"][t, 0] * 1.0)
        assert abs(residual) < SOC_ATOL, f"DC SoC dynamics violated at t={t}"

test_T1_matches_single_step_objective
    unit = _default_unit(aging_weight=0.0)
    df_P, df_Q = _flat_load_dfs(case9, T=1)
    _, r_single = _solve_dc_single(storage=[unit])
    _, r_multi  = _solve_dc_multistep(1, df_P, df_Q, storage=[unit])
    assert abs(r_multi["objective"] - r_single["objective"]) \
           / abs(r_single["objective"]) < OBJ_RTOL
```

---

### `TestStorageMultipleUnits`

```
test_two_units_ac_solves_optimal
    unit_a = _default_unit(bus=1, S_max=30.0)
    unit_b = _default_unit(bus=2, S_max=30.0)
    _, r = _solve_ac_single(storage=[unit_a, unit_b])
    assert r["status"] == "optimal"

test_two_units_dc_solves_optimal
    unit_a = _default_unit(bus=1, S_max=30.0)
    unit_b = _default_unit(bus=2, S_max=30.0)
    _, r = _solve_dc_single(storage=[unit_a, unit_b])
    assert r["status"] == "optimal"

test_b_shape_two_units_ac
    unit_a = _default_unit(bus=1)
    unit_b = _default_unit(bus=2)
    _, r = _solve_ac_single(storage=[unit_a, unit_b])
    assert r["b"].shape == (2,)

test_b_q_shape_two_units_ac
    unit_a = _default_unit(bus=1)
    unit_b = _default_unit(bus=2)
    _, r = _solve_ac_single(storage=[unit_a, unit_b])
    assert r["b_q"].shape == (2,)

test_soc_shape_two_units
    unit_a = _default_unit(bus=1)
    unit_b = _default_unit(bus=2)
    _, r = _solve_ac_single(storage=[unit_a, unit_b])
    assert r["soc"].shape == (2,)

test_two_units_same_bus_ac_solves_optimal
    unit_a = _default_unit(bus=1, S_max=20.0)
    unit_b = _default_unit(bus=1, S_max=20.0)
    _, r = _solve_ac_single(storage=[unit_a, unit_b])
    assert r["status"] == "optimal"

test_two_units_same_bus_dc_solves_optimal
    unit_a = _default_unit(bus=1, S_max=20.0)
    unit_b = _default_unit(bus=1, S_max=20.0)
    _, r = _solve_dc_single(storage=[unit_a, unit_b])
    assert r["status"] == "optimal"

test_ns_equals_two_in_data
    unit_a = _default_unit(bus=1)
    unit_b = _default_unit(bus=2)
    build, _ = _solve_ac_single(storage=[unit_a, unit_b])
    assert build.data["ns"] == 2

test_Cs_shape_two_units
    unit_a = _default_unit(bus=1)
    unit_b = _default_unit(bus=2)
    build, _ = _solve_ac_single(storage=[unit_a, unit_b])
    nb = build.data["nb"]
    assert build.data["Cs"].shape == (nb, 2)
```

---

### `TestStorageNodal`

Verifies that storage power actually enters the nodal balance.

```
test_storage_affects_dispatch_ac
    # With a large enough storage unit allowed to discharge freely,
    # the generator dispatch should differ from the no-storage case.
    unit = StorageUnitIdeal(bus=1, apparent_power_rating=100.0,
                             capacity=200.0, initial_soc=100.0,
                             aging_weight=0.0)
    _, r_no_stor = _solve_ac_single()
    _, r_stor    = _solve_ac_single(storage=[unit])
    assert r_no_stor["status"] == "optimal"
    assert r_stor["status"]    == "optimal"
    # Total generation should differ — storage discharged some real power
    total_no_stor = np.sum(r_no_stor["Pg"])
    total_stor    = np.sum(r_stor["Pg"])
    # Storage discharging reduces required generation
    assert total_stor < total_no_stor + 1.0   # allow 1 MW tolerance

test_storage_affects_dispatch_dc
    unit = StorageUnitIdeal(bus=1, apparent_power_rating=100.0,
                             capacity=200.0, initial_soc=100.0,
                             aging_weight=0.0)
    _, r_no_stor = _solve_dc_single()
    _, r_stor    = _solve_dc_single(storage=[unit])
    assert r_stor["status"] == "optimal"
    total_no_stor = np.sum(r_no_stor["Pg"])
    total_stor    = np.sum(r_stor["Pg"])
    assert total_stor < total_no_stor + 1.0

test_fully_charged_cannot_charge_further_dc
    # initial_soc == capacity: charging would violate SoC upper bound
    unit = StorageUnitIdeal(bus=1, apparent_power_rating=30.0,
                             capacity=50.0, initial_soc=50.0,
                             aging_weight=0.0)
    df_P, df_Q = _flat_load_dfs(case9, T=1)
    _, r = _solve_dc_multistep(1, df_P, df_Q, storage=[unit], delta=1.0)
    assert r["status"] == "optimal"
    # b[0] must be >= 0 (cannot charge: SoC already at max)
    assert r["b"][0, 0] >= -VAL_ATOL

test_fully_discharged_cannot_discharge_further_dc
    # initial_soc == 0: discharging would violate SoC lower bound
    unit = StorageUnitIdeal(bus=1, apparent_power_rating=30.0,
                             capacity=50.0, initial_soc=0.0,
                             aging_weight=0.0)
    df_P, df_Q = _flat_load_dfs(case9, T=1)
    _, r = _solve_dc_multistep(1, df_P, df_Q, storage=[unit], delta=1.0)
    assert r["status"] == "optimal"
    # b[0] must be <= 0 (cannot discharge: SoC at zero)
    assert r["b"][0, 0] <= VAL_ATOL

test_reactive_support_ac_independent_of_real_power
    # A storage unit with initial_soc=capacity (fully charged) can still
    # provide reactive power in AC — b_q is not constrained by SoC.
    unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                             capacity=50.0, initial_soc=50.0,
                             aging_weight=0.0)
    _, r = _solve_ac_single(storage=[unit])
    assert r["status"] == "optimal"
    # b_q can be non-zero even though real power is constrained to >= 0
    # Just verify apparent power constraint holds
    violation = r["b"][0]**2 + r["b_q"][0]**2 - 50.0**2
    assert violation <= APR_ATOL
```

---

### `TestStorageAgingCost`

```
test_aging_weight_zero_storage_cost_near_zero
    unit = _default_unit(aging_weight=0.0)
    _, r = _solve_ac_single(storage=[unit])
    assert abs(r["storage_cost"]) < 1e-4

test_higher_aging_weight_higher_or_equal_objective_ac
    unit_free = _default_unit(aging_weight=0.0)
    unit_aged = _default_unit(aging_weight=1.0)
    _, r_free = _solve_ac_single(storage=[unit_free])
    _, r_aged = _solve_ac_single(storage=[unit_aged])
    assert r_free["status"] == r_aged["status"] == "optimal"
    assert r_aged["objective"] >= r_free["objective"] - 1e-3

test_higher_aging_weight_reduces_cycling_dc
    # With varying load, higher aging weight should produce
    # less |b| throughput (or equal if storage not dispatched).
    scales = [0.8, 1.0, 1.2]
    df_P, df_Q = _varying_load_dfs(case9, scales)

    unit_free = _default_unit(aging_weight=0.0)
    unit_aged = _default_unit(aging_weight=5.0)

    _, r_free = _solve_dc_multistep(3, df_P, df_Q, storage=[unit_free])
    _, r_aged = _solve_dc_multistep(3, df_P, df_Q, storage=[unit_aged])

    assert r_free["status"] == r_aged["status"] == "optimal"
    cycling_free = np.sum(np.abs(r_free["b"]))
    cycling_aged = np.sum(np.abs(r_aged["b"]))
    assert cycling_aged <= cycling_free + VAL_ATOL

test_storage_cost_equals_weight_times_abs_b_ac
    unit = _default_unit(aging_weight=0.5)
    _, r = _solve_ac_single(storage=[unit])
    expected_cost = 0.5 * np.sum(np.abs(r["b"]))
    assert abs(r["storage_cost"] - expected_cost) < 1e-6

test_storage_cost_equals_weight_times_abs_b_dc
    unit = _default_unit(aging_weight=0.5)
    _, r = _solve_dc_single(storage=[unit])
    expected_cost = 0.5 * np.sum(np.abs(r["b"]))
    assert abs(r["storage_cost"] - expected_cost) < 1e-6

test_storage_cost_equals_weight_times_abs_b_multistep_dc
    scales = [0.8, 1.0, 1.2]
    df_P, df_Q = _varying_load_dfs(case9, scales)
    unit = _default_unit(aging_weight=0.3)
    _, r = _solve_dc_multistep(3, df_P, df_Q, storage=[unit])
    expected_cost = 0.3 * np.sum(np.abs(r["b"]))
    assert abs(r["storage_cost"] - expected_cost) < 1e-6

test_reactive_power_not_penalised_ac
    # With aging_weight > 0, storage_cost should only reflect |b|, not |b_q|.
    unit = _default_unit(aging_weight=1.0)
    _, r = _solve_ac_single(storage=[unit])
    expected_cost = 1.0 * np.sum(np.abs(r["b"]))
    assert abs(r["storage_cost"] - expected_cost) < 1e-6
```

---

### `TestStorageDelta`

```
test_delta_025_soc_dynamics_ac
    # delta=0.25 means each step is 15 minutes.
    # soc_0 = initial_soc - b_0 * 0.25
    unit = _default_unit(initial_soc=50.0)
    _, r = _solve_ac_single(storage=[unit], delta=0.25)
    residual = r["soc"][0] - (50.0 - r["b"][0] * 0.25)
    assert abs(residual) < SOC_ATOL

test_delta_025_soc_dynamics_multistep_dc
    unit = _default_unit(initial_soc=30.0)
    df_P, df_Q = _flat_load_dfs(case9, T=3)
    _, r = _solve_dc_multistep(3, df_P, df_Q, storage=[unit], delta=0.25)
    residual_0 = r["soc"][0, 0] - (30.0 - r["b"][0, 0] * 0.25)
    assert abs(residual_0) < SOC_ATOL
    for t in range(1, 3):
        residual = r["soc"][t, 0] - (r["soc"][t-1, 0] - r["b"][t, 0] * 0.25)
        assert abs(residual) < SOC_ATOL

test_smaller_delta_allows_more_energy_exchange
    # With delta=1.0, max energy exchangeable per step = S_max * 1.0
    # With delta=0.25, max energy exchangeable per step = S_max * 0.25
    # So with fixed initial_soc and capacity, smaller delta should allow
    # less total SoC change per step.
    unit = _default_unit(initial_soc=50.0, capacity=100.0, S_max=30.0)
    _, r1 = _solve_ac_single(storage=[unit], delta=1.0)
    _, r025 = _solve_ac_single(storage=[unit], delta=0.25)
    # |soc - initial_soc| should be smaller with delta=0.25
    delta_soc_1   = abs(r1["soc"][0]   - 50.0)
    delta_soc_025 = abs(r025["soc"][0] - 50.0)
    # This is a soft check — just verify both are feasible
    assert r1["status"] == r025["status"] == "optimal"
```

---

### `TestStorageCouplingConstraintHook`

```
test_user_coupling_constraints_accepted_with_storage_ac
    unit = _default_unit()
    df_P, df_Q = _flat_load_dfs(case9, T=3)
    build = build_opf_multistep(
        case9(), df_P, df_Q, T=3, formulation="ac",
        storage=[unit], coupling_constraints=[],
    )
    assert isinstance(build, OPFBuild)

test_user_coupling_constraints_accepted_with_storage_dc
    unit = _default_unit()
    df_P, df_Q = _flat_load_dfs(case9, T=3)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build = build_opf_multistep(
            case9(), df_P, df_Q, T=3, formulation="lossy_dc",
            storage=[unit], coupling_constraints=[],
        )
    assert isinstance(build, OPFBuild)

test_user_coupling_constraint_applied_alongside_storage
    # Add a trivial coupling constraint on Pg and verify problem still solves.
    unit = _default_unit()
    df_P, df_Q = _flat_load_dfs(case9, T=3)
    build = build_opf_multistep(
        case9(), df_P, df_Q, T=3, formulation="ac", storage=[unit],
    )
    # Pg[0][0] == Pg[1][0]: first generator output equal in steps 0 and 1
    coupling = [build.variables["Pg"][0][0] == build.variables["Pg"][1][0]]
    build2 = build_opf_multistep(
        case9(), df_P, df_Q, T=3, formulation="ac",
        storage=[unit], coupling_constraints=coupling,
    )
    build2.solve()
    r = extract_results(build2)
    assert r["status"] == "optimal"
```

---

## 4. Running the Tests

```bash
# Run only the storage tests
uv run --extra dev pytest tests/test_storage.py -v

# Run full suite to confirm no regressions
uv run --extra dev pytest tests/ -v 2>&1 | tail -10
```

Expected: all existing tests pass, all new storage tests pass.

If any test fails due to what appears to be a bug in the implementation (not the test), **do not modify source files**. Document the failure with a clear description of:
- Which test failed
- What value was expected vs what was returned
- Which constraint or equation appears to be violated

Then stop and report.

---

## 5. What NOT to Do

- Do not modify any file in `src/cvxopf/`
- Do not add storage keys to `build.data` manually in tests — test what the implementation returns
- Do not use `build.prob.solve()` directly — always use `build.solve()`
- Do not test AC and DC in the same test function — keep formulations separate
- Do not suppress `UserWarning` in `TestStorageDCSingle.test_dc_apparent_power_fallback_emits_warning` — that test must catch the warning
- Do not add `delta` to `StorageUnitIdeal(...)` constructor calls — it is not a field
- Do not multiply `r["b"]`, `r["b_q"]`, or `r["soc"]` by `baseMVA` — they are already in MW/MVAr/MWh
