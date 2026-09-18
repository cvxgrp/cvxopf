"""
Shared pytest fixtures.
"""

import json
from functools import cache
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cvxopf.testcases import case9, case14


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def p0_nominal_report(tmp_path_factory):
    """Compute each requested horizon once; reports contain only frozen values."""
    from experiments.case118_annual_hierarchy.p0_equivalence import (
        run_nominal_equivalence,
    )

    @cache
    def run(horizon_steps):
        return run_nominal_equivalence(
            horizon_steps, tmp_path_factory.mktemp(f"p0-nominal-{horizon_steps}h")
        )

    return run


@pytest.fixture(scope="session")
def p0_injected_report(tmp_path_factory):
    """Run only requested frozen cases, sharing results across their assertions."""
    from experiments.case118_annual_hierarchy.p0_injected_equivalence import (
        INJECTED_CASES, run_injected_equivalence,
    )

    cases = {case.name: case for case in INJECTED_CASES}

    @cache
    def run(case_name):
        return run_injected_equivalence(
            cases[case_name], tmp_path_factory.mktemp(f"p0-injected-{case_name}")
        )

    return run


@pytest.fixture(scope="session")
def p0_persistence_report(tmp_path_factory):
    from experiments.case118_annual_hierarchy.p0_persistence_gate import (
        run_persistence_gate,
    )

    return run_persistence_gate(tmp_path_factory.mktemp("p0-persistence"))


@pytest.fixture(scope="session")
def verified_s5_manifest_bytes():
    from experiments.case118_annual_hierarchy import s4b_manifest

    # Validate the real frozen input once; keep bytes so callers cannot mutate it.
    return s4b_manifest.canonical_json(s4b_manifest.load_verified_manifest())


@pytest.fixture
def reuse_verified_s5_manifest(monkeypatch, verified_s5_manifest_bytes):
    """Opt-in workflow fixture; dedicated manifest validation stays unpatched."""
    from experiments.case118_annual_hierarchy import (
        s4b_execution, s4b_manifest, s5_execution,
    )

    def load(path=s4b_manifest.S4B_MANIFEST_PATH):
        if path != s4b_manifest.S4B_MANIFEST_PATH:
            return s4b_manifest.load_verified_manifest(path)
        # Match the loader's fresh-object contract, including nested shard data.
        return json.loads(verified_s5_manifest_bytes)

    monkeypatch.setattr(s4b_execution, "load_verified_manifest", load)
    monkeypatch.setattr(s5_execution, "load_verified_manifest", load)


@pytest.fixture
def case9_raw():
    return case9()


@pytest.fixture
def case14_raw():
    return case14()


@pytest.fixture
def case9_ref():
    path = FIXTURES / "case9_pypower_reference.json"
    with open(path) as f:
        data = json.load(f)
    return {k: np.asarray(v) if isinstance(v, list) else v
            for k, v in data.items()}


@pytest.fixture
def case14_ref():
    path = FIXTURES / "case14_pypower_reference.json"
    with open(path) as f:
        data = json.load(f)
    return {k: np.asarray(v) if isinstance(v, list) else v
            for k, v in data.items()}


@pytest.fixture
def case57_ref():
    path = FIXTURES / "case57_pypower_reference.json"
    with open(path) as f:
        data = json.load(f)
    return {k: np.asarray(v) if isinstance(v, list) else v
            for k, v in data.items()}


@pytest.fixture
def case9_multistep_load():
    """
    Three-step load DataFrames for case9 (9 buses).
    Row 0: 80% of base load.
    Row 1: 100% of base load (identical to single-step).
    Row 2: 120% of base load.
    """
    case = case9()
    Pd_base = case["bus"][:, 2]   # MW
    Qd_base = case["bus"][:, 3]   # MVAr

    scales = [0.8, 1.0, 1.2]
    Pd_data = np.outer(scales, Pd_base)
    Qd_data = np.outer(scales, Qd_base)

    df_P = pd.DataFrame(Pd_data)
    df_Q = pd.DataFrame(Qd_data)
    return df_P, df_Q
