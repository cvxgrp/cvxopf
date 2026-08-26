"""M14a frozen characterization of the legacy temporal graph."""

from contextlib import nullcontext
from dataclasses import FrozenInstanceError, asdict
import hashlib
import json

import cvxpy as cp
import numpy as np
import pandas as pd
import pytest

from cvxopf import build_opf, build_opf_multistep
from cvxopf.characterization import (
    NamedShape,
    characterize_convex_canonicalization,
    characterize_source_graph,
)
from cvxopf.testcases import case9


SOURCE_GRAPH_DIGESTS = {
    "ac": "60df7473cf05f07b852406b6c98dea51af5491efd5d9970d2265b569f9862e07",
    "lossy_dc": "0f35650806830f468c1719afec5f3051331bb7a44820cc70b2a9e8b8044ee497",
    "singlenode_dc": (
        "7db1ad0a263a5b049221db2ffcccf1201d49e3677fcc2f86fee0d951e1894f27"
    ),
}

REDUCTION_CHAIN = (
    "Dcp2Cone",
    "CvxAttr2Constr",
    "EliminateZeroSized",
    "ConeMatrixStuffing",
    "CLARABEL",
)

CANONICAL_BASELINES = {
    "lossy_dc": (48, 24, 66, 0, (), (), (), 90, 48, 156, 18, REDUCTION_CHAIN),
    "singlenode_dc": (12, 8, 12, 0, (), (), (), 20, 12, 30, 6, REDUCTION_CHAIN),
}


def _digest(value: object) -> str:
    encoded = json.dumps(asdict(value), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _frames(T: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    ppc = case9()
    return (
        pd.DataFrame(np.tile(ppc["bus"][:, 2], (T, 1))),
        pd.DataFrame(np.tile(ppc["bus"][:, 3], (T, 1))),
    )


@pytest.mark.parametrize(
    ("formulation", "expected"),
    [
        ("ac", (264, 254, 60, 24, 170)),
        ("lossy_dc", (24, 18, 30, 4, 8)),
        ("singlenode_dc", (6, 2, 12, 2, 6)),
    ],
)
def test_stepwise_source_graph_baseline(formulation, expected):
    active, reactive = _frames(2)
    context = pytest.warns(UserWarning) if formulation != "ac" else nullcontext()
    with context:
        build = build_opf_multistep(
            case9(), active, reactive, T=2, formulation=formulation
        )

    record = characterize_source_graph(build)

    assert build.temporal_assembly == "stepwise"
    assert "temporal_assembly" not in build.data
    assert record.temporal_assembly == "stepwise"
    assert record.horizon == 2
    assert record.parameter_schema == (NamedShape("load_inv_base_mva", ((), ())),)
    assert (
        record.scalar_variables,
        record.scalar_equalities,
        record.scalar_inequalities,
        record.variable_object_count,
        record.constraint_object_count,
    ) == expected
    # This digest binds the complete variable/expression/parameter/data schema
    # and every source-graph count, while the assertions above keep the main
    # scientific dimensions readable in a failure report.
    assert _digest(record) == SOURCE_GRAPH_DIGESTS[formulation]


def test_temporal_selector_is_closed_and_vectorized_is_reserved():
    active, reactive = _frames(1)
    with pytest.raises(ValueError, match="temporal_assembly"):
        build_opf_multistep(
            case9(),
            active,
            reactive,
            T=1,
            temporal_assembly="other",  # type: ignore[arg-type]
        )
    with pytest.raises(NotImplementedError, match="M14b"):
        build_opf_multistep(
            case9(),
            active,
            reactive,
            T=1,
            temporal_assembly="vectorized",
        )


def test_single_step_build_records_stepwise_provenance():
    build = build_opf(case9(), formulation="lossy_dc")
    assert build.temporal_assembly == "stepwise"
    assert characterize_source_graph(build).horizon is None


@pytest.mark.parametrize("formulation", ["lossy_dc", "singlenode_dc"])
def test_cpp_and_scipy_characterizations_retain_backend_identity(formulation):
    active, _reactive = _frames(2)
    build = build_opf_multistep(case9(), active, T=2, formulation=formulation)

    cpp = characterize_convex_canonicalization(build, backend="CPP")
    scipy = characterize_convex_canonicalization(build, backend="SCIPY")

    assert cpp.backend == "CPP"
    assert scipy.backend == "SCIPY"
    assert cpp.solver == str(cp.CLARABEL)
    assert cpp.canonical_variable_count == scipy.canonical_variable_count
    assert cpp.equality_rows == scipy.equality_rows
    assert cpp.nonnegative_rows == scipy.nonnegative_rows
    assert cpp.coefficient_nonzeros == scipy.coefficient_nonzeros
    assert cpp.quadratic_nonzeros == scipy.quadratic_nonzeros
    expected = CANONICAL_BASELINES[formulation]
    for record in (cpp, scipy):
        assert (
            record.canonical_variable_count,
            record.equality_rows,
            record.nonnegative_rows,
            record.exponential_cones,
            record.second_order_cones,
            record.positive_semidefinite_cones,
            record.power_cones_3d,
            record.coefficient_rows,
            record.coefficient_columns,
            record.coefficient_nonzeros,
            record.quadratic_nonzeros,
            record.reduction_chain,
        ) == expected


def test_characterization_records_are_immutable():
    build = build_opf(case9(), formulation="singlenode_dc")
    record = characterize_source_graph(build)
    with pytest.raises(FrozenInstanceError):
        record.horizon = 5  # type: ignore[misc]


def test_convex_canonicalizer_rejects_ac_dnlp():
    build = build_opf(case9(), formulation="ac")
    with pytest.raises(ValueError, match="AC/DNLP"):
        characterize_convex_canonicalization(build)
