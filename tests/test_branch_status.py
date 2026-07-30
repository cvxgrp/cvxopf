"""Cross-formulation contract tests for MATPOWER branch status."""

import numpy as np
import pytest

from cvxopf.problem import build_opf
from cvxopf.testcases import case9


@pytest.mark.parametrize("formulation", ["ac", "lossy_dc", "singlenode_dc"])
@pytest.mark.parametrize("invalid_status", [-1.0, 2.0, 0.5, np.nan])
def test_invalid_branch_status_rejected(formulation, invalid_status):
    case = case9()
    case["branch"][0, 10] = invalid_status

    with pytest.raises(
        ValueError,
        match=r"BR_STATUS values must be exactly 0 or 1.*row 0",
    ):
        build_opf(case, formulation=formulation)


@pytest.mark.parametrize("formulation", ["ac", "lossy_dc", "singlenode_dc"])
def test_inactive_branch_status_accepted(formulation):
    case = case9()
    case["branch"][0, 10] = 0

    build = build_opf(case, formulation=formulation)

    assert build.formulation == formulation


@pytest.mark.parametrize(
    "malformed_branch",
    [
        np.zeros(10),
        np.zeros((1, 10)),
    ],
)
def test_singlenode_malformed_branch_shape_rejected(malformed_branch):
    case = case9()
    case["branch"] = malformed_branch

    with pytest.raises(
        ValueError,
        match=r"branch array must be two-dimensional and include.*BR_STATUS",
    ):
        build_opf(case, formulation="singlenode_dc")


@pytest.mark.parametrize(
    "formulation", ["ac", "lossy_dc", "singlenode_dc"]
)
@pytest.mark.parametrize(
    ("table", "column", "name"),
    [
        ("bus", 0, "BUS_I"),
        ("branch", 0, "F_BUS"),
        ("branch", 1, "T_BUS"),
        ("gen", 0, "GEN_BUS"),
    ],
)
@pytest.mark.parametrize("value", [1.5, np.nan, np.inf])
def test_nonintegral_or_nonfinite_case_identifier_rejected(
    formulation,
    table,
    column,
    name,
    value,
):
    case = case9()
    case[table] = case[table].astype(float)
    case[table][0, column] = value

    with pytest.raises(
        ValueError,
        match=rf"{name} values must be finite integers.*row 0",
    ):
        build_opf(case, formulation=formulation)
