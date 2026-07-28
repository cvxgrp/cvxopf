"""
Shared pytest fixtures.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cvxopf.testcases import case9, case14


FIXTURES = Path(__file__).parent / "fixtures"


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
