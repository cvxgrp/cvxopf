"""Pre-refactor characterization gates for the M16+ adapter migration."""

import ast
import itertools
from pathlib import Path
import warnings

import cvxpy as cp
import numpy as np
import pandas as pd
import pytest

from cvxopf import HVDCLink, NondispatchableUnit, StorageUnitIdeal
from cvxopf.problem import build_opf, build_opf_multistep
from cvxopf.results import extract_results
from cvxopf.testcases import case9


FORMULATIONS = ("ac", "lossy_dc", "singlenode_dc")
COMPONENT_FLAGS = tuple(itertools.product((False, True), repeat=3))

CORE_VARIABLES = {
    "ac": {"theta", "v", "P_vec", "Q_vec", "p", "q", "Pg", "Qg"},
    "lossy_dc": {"p_flows", "Pg"},
    "singlenode_dc": {"Pg"},
}
CORE_EXPRESSIONS = {
    "ac": {"p_net", "q_net"},
    "lossy_dc": {"p_net"},
    "singlenode_dc": {"p_net"},
}
CORE_DATA = {
    "ac": {
        "baseMVA", "nb", "ng", "ref", "pv", "ext_to_int", "Ybus", "G", "B",
        "E", "Z", "Cg", "gen_bus", "Pgmin", "Pgmax", "Qgmin", "Qgmax",
        "gencost", "rows", "cols", "G_vec", "B_vec", "Rp",
    },
    "lossy_dc": {
        "baseMVA", "nb", "ng", "nl", "ext_to_int", "A", "Cg", "r",
        "f_max", "gen_bus", "Pgmin", "Pgmax", "gencost", "loss_weight",
    },
    "singlenode_dc": {
        "baseMVA", "nb", "source_nb", "ng", "ext_to_int", "Cg", "gen_bus",
        "Pgmin", "Pgmax", "gencost",
    },
}

STORAGE_DATA = {
    "ns", "Cs", "storage_bus", "storage_apparent_power_rating",
    "storage_capacity", "storage_initial_soc", "storage_delta",
    "storage_aging_weight", "storage_terminal_soc",
    "storage_terminal_constraint", "storage_terminal_cost",
    "storage_terminal_weight",
}
ND_DATA = {"nnd", "Cnd", "nd_bus", "nd_apparent_power_rating"}
HVDC_DATA = {"n_hvdc", "Ch_from", "Ch_to"}

BASELINE = {
    ("ac", False): {
        "objective": 4817.72129047,
        "Pg": [83.22337487, 126.23874945, 88.53147794],
        "Qg": [9.40548902, -10.50001756, -32.12129472],
        "b": [-0.0],
        "b_q": [17.00289061],
        "soc": [20.0],
        "q_nd": [2.58111146],
        "Vm": [
            1.09909099, 1.09142080, 1.08154984,
            1.09492955, 1.08915694, 1.10000000,
            1.09614755, 1.10000000, 1.07235083,
        ],
    },
    ("ac", True): {
        "objective": 9753.28343256,
        "Pg": [
            [83.92991185, 127.16748532, 89.17705212],
            [84.21200399, 127.32134249, 89.30697354],
        ],
        "Qg": [
            [9.44725127, -10.41098036, -32.07049376],
            [9.65261849, -10.38387394, -31.99060381],
        ],
        "b": [[-2.28140167], [2.28140167]],
        "b_q": [[17.16780520], [16.84943982]],
        "soc": [[22.28140167], [20.0]],
        "q_nd": [[2.53951319], [3.07481667]],
        "Vm": [
            [
                1.09923295, 1.09143541, 1.08156226,
                1.09506390, 1.08924273, 1.10000000,
                1.09607497, 1.10000000, 1.07247149,
            ],
            [
                1.09893404, 1.09144496, 1.08160319,
                1.09466232, 1.08853338, 1.10000000,
                1.09622267, 1.10000000, 1.07205291,
            ],
        ],
    },
    ("lossy_dc", False): {
        "objective": 4751.22787191,
        "Pg": [80.33134163, 126.30917637, 88.45948202],
        "b": [0.0],
        "soc": [20.0],
    },
    ("lossy_dc", True): {
        "objective": 9616.29991644,
        "Pg": [
            [81.06904301, 127.26386958, 89.12192222],
            [81.16004745, 127.38153639, 89.20358136],
        ],
        "b": [[-2.35483481], [2.35483481]],
        "soc": [[22.35483481], [20.0]],
    },
    ("singlenode_dc", False): {
        "objective": 4748.92694654,
        "Pg": [80.29898460, 126.26927415, 88.43174127],
        "b": [-0.0],
        "soc": [20.0],
    },
    ("singlenode_dc", True): {
        "objective": 9611.66207864,
        "Pg": [
            [81.03671922, 127.22398958, 89.09419685],
            [81.12762828, 127.34163660, 89.17582948],
        ],
        "b": [[-2.35490565], [2.35490565]],
        "soc": [[22.35490565], [20.0]],
    },
}


def _components():
    storage = StorageUnitIdeal(
        bus=7,
        apparent_power_rating=20.0,
        capacity=40.0,
        initial_soc=20.0,
        aging_weight=0.01,
        terminal_soc=20.0,
        terminal_constraint="equality",
    )
    nd = NondispatchableUnit(
        bus=5,
        p_available=20.0,
        apparent_power_rating=25.0,
        device_id="nd",
    )
    link = HVDCLink(
        from_bus=1,
        to_bus=2,
        p_min_mw=-5.0,
        p_max_mw=-5.0,
        loss_percent=2.0,
        device_id="hvdc",
    )
    return storage, nd, link


def _cvxpy_variable_calls(source):
    """Return CVXPY Variable constructor calls under common import styles."""
    tree = ast.parse(source)
    module_aliases = set()
    variable_aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module_aliases.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "cvxpy"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "cvxpy":
            variable_aliases.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "Variable"
            )
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in module_aliases
                and node.func.attr == "Variable"
            )
            or (
                isinstance(node.func, ast.Name)
                and node.func.id in variable_aliases
            )
        )
    ]


def _build(
    formulation,
    multistep,
    *,
    with_storage,
    with_nd,
    with_hvdc,
):
    case = case9()
    storage, nd, link = _components()
    kwargs = {
        "storage": [storage] if with_storage else None,
        "nondispatchable": [nd] if with_nd else None,
        "hvdc": [link] if with_hvdc else None,
    }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        if not multistep:
            return build_opf(case, formulation=formulation, **kwargs)
        T = 2
        df_P = pd.DataFrame(np.tile(case["bus"][:, 2], (T, 1)))
        df_Q = pd.DataFrame(np.tile(case["bus"][:, 3], (T, 1)))
        if with_nd:
            kwargs["df_nd"] = pd.DataFrame({"nd": [20.0, 15.0]})
        return build_opf_multistep(
            case, df_P, df_Q, T=T, formulation=formulation, **kwargs
        )


@pytest.mark.parametrize("formulation", FORMULATIONS)
@pytest.mark.parametrize("multistep", [False, True])
@pytest.mark.parametrize(
    ("with_storage", "with_nd", "with_hvdc"), COMPONENT_FLAGS
)
def test_build_schema_is_additive_by_component(
    formulation, multistep, with_storage, with_nd, with_hvdc
):
    build = _build(
        formulation,
        multistep,
        with_storage=with_storage,
        with_nd=with_nd,
        with_hvdc=with_hvdc,
    )
    variables = set(CORE_VARIABLES[formulation])
    data = set(CORE_DATA[formulation])
    expressions = set(CORE_EXPRESSIONS[formulation])
    if multistep:
        data.update({"T", "Pd_series"})
    else:
        data.add("Pd_total" if formulation == "singlenode_dc" else "Pd")
    if formulation == "ac":
        data.add("Qd_series" if multistep else "Qd")

    if with_storage:
        variables.update({"b", "soc"})
        if formulation == "ac":
            variables.add("b_q")
        data.update(STORAGE_DATA)
        expressions.add("storage_cost")
    if with_nd:
        variables.add("p_nd")
        if formulation == "ac":
            variables.add("q_nd")
        data.update(ND_DATA)
        data.add("nd_available" if multistep else "nd_p_available")
    if with_hvdc and formulation != "singlenode_dc":
        variables.update({"p_hvdc_in", "p_hvdc_out"})
        data.update(HVDC_DATA)

    assert set(build.variables) == variables
    assert set(build.data) == data
    assert set(build.expressions) == expressions
    for variable in build.variables.values():
        if multistep:
            assert isinstance(variable, list)
            assert len(variable) == 2
            assert all(isinstance(item, cp.Variable) for item in variable)
        else:
            assert isinstance(variable, cp.Variable)
    for name, expression in build.expressions.items():
        if multistep and name in {"p_net", "q_net"}:
            assert isinstance(expression, list)
            assert len(expression) == 2
            assert all(isinstance(item, cp.Expression) for item in expression)
        else:
            assert isinstance(expression, cp.Expression)


@pytest.mark.parametrize("formulation", FORMULATIONS)
@pytest.mark.parametrize("multistep", [False, True])
def test_mixed_component_numerical_baseline(formulation, multistep):
    build = _build(
        formulation,
        multistep,
        with_storage=True,
        with_nd=True,
        with_hvdc=True,
    )
    build.solve()
    results = extract_results(build)
    expected = BASELINE[(formulation, multistep)]

    assert results["status"] == "optimal"
    assert build.prob.is_dcp() is (formulation != "ac")
    assert results["objective"] == pytest.approx(
        expected["objective"], rel=1e-5, abs=1e-3
    )
    np.testing.assert_allclose(
        results["Pg"], expected["Pg"], rtol=1e-4, atol=5e-2
    )
    np.testing.assert_allclose(
        results["b"], expected["b"], rtol=1e-4, atol=5e-2
    )
    expected_nd = [[20.0], [15.0]] if multistep else [20.0]
    np.testing.assert_allclose(results["p_nd"], expected_nd, atol=1e-5)
    np.testing.assert_allclose(results["soc"], expected["soc"], atol=5e-2)
    if formulation == "ac":
        np.testing.assert_allclose(results["Qg"], expected["Qg"], atol=2.5e-1)
        np.testing.assert_allclose(
            results["b_q"], expected["b_q"], atol=2.5e-1
        )
        np.testing.assert_allclose(
            results["q_nd"], expected["q_nd"], atol=2.5e-1
        )
        np.testing.assert_allclose(results["Vm"], expected["Vm"], atol=2e-3)
    if formulation == "singlenode_dc":
        assert "p_hvdc_in" not in results
    else:
        np.testing.assert_allclose(results["p_hvdc_in"], -5.0, atol=1e-5)
        np.testing.assert_allclose(results["p_hvdc_out"], 4.9, atol=1e-5)
        np.testing.assert_allclose(results["hvdc_loss"], 0.1, atol=1e-5)


@pytest.mark.parametrize(
    "source",
    [
        "import cvxpy as cp\nx = cp.Variable(1)\n",
        "import cvxpy\nx = cvxpy.Variable(1)\n",
        "from cvxpy import Variable as Var\nx = Var(1)\n",
    ],
)
def test_variable_ownership_gate_recognizes_cvxpy_import_styles(source):
    assert len(_cvxpy_variable_calls(source)) == 1


@pytest.mark.parametrize(
    "module_name", ["generator", "storage", "nondispatchable", "hvdc"]
)
def test_component_modules_do_not_create_cvxpy_variables(module_name):
    module_path = (
        Path(__file__).parents[1] / "src" / "cvxopf" / f"{module_name}.py"
    )
    assert _cvxpy_variable_calls(module_path.read_text()) == []
