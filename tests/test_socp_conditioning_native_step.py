"""Solver-free checks of the observational native replay boundary."""

import numpy as np
from scipy import sparse
from types import SimpleNamespace
import json

from experiments.socp_conditioning import native_step_probe as n
from experiments.socp_conditioning import native_step_analysis as a


def test_sparse_serialization_preserves_exact_coordinates():
    source = sparse.csc_matrix([[0.0, 0.3, 0.0], [1e-12, 0.0, -8.0]])
    record = n.matrix_json(source)
    recovered = sparse.csc_matrix(
        (record["nzval"], record["rowval"], record["colptr"]),
        shape=(record["m"], record["n"]),
    )
    assert np.array_equal(source.toarray(), recovered.toarray())


def test_replay_requires_every_primal_slack_and_dual_coordinate():
    source = {key: [1.0, 2.0] for key in ("x", "s", "z")}
    assert n.same_solution(source, source)
    for key in source:
        changed = source | {key: [1.0, np.nextafter(2.0, 3.0)]}
        assert not n.same_solution(source, changed)


def test_input_exact_arrays_cones_and_settings_without_solving(tmp_path):
    data = dict(
        P=sparse.eye(2, format="csc"),
        A=sparse.csc_matrix(np.arange(12).reshape(6, 2)),
        c=np.array([0.1, -0.3]),
        b=np.zeros(6),
        dims=SimpleNamespace(zero=1, nonneg=2, soc=[3], exp=0, psd=[], p3d=[]),
    )
    payload = n.native_input(tmp_path, data, False, n.m.options("CLARABEL"))
    assert payload["cones"] == [
        {"ZeroConeT": 1},
        {"NonnegativeConeT": 2},
        {"SecondOrderConeT": 3},
    ]
    assert payload["q"] == data["c"].tolist()
    assert payload["settings"]["min_terminate_step_length"] == 1e-8
    assert payload["settings"]["time_limit"] == np.finfo(float).max
    assert json.loads((tmp_path / "native_input.json").read_text()) == payload


def test_high_precision_cone_boundary():
    report = a.soc_boundary([2.0, 1.0, 0.0], [0.0, 1.0, 0.0])
    assert float(report["cone_margin"]) == 1.0
    assert report["first_nonnegative_boundary"] == 1.0


def test_refinement_records_distinguish_finite_from_accurate():
    text = """TRACE ITER iter=30 mu=1e-12
TRACE REFINE initial=1e25 rhs_inf=1e4 tolerance=1e-9 regularizer=1e-8
TRACE REFINE candidate=1e41 previous=1e25
TRACE REFINE final=1e25 rhs_inf=1e4 meets_tolerance=false
TRACE STEP direction=Combined cone_limit=1e-39
"""
    solves, steps, _, _ = a.parse_trace(text)
    assert solves[0]["iteration"] == 30
    assert not solves[0]["meets_tolerance"]
    assert np.isclose(solves[0]["relative_to_rhs"], 1e21)
    assert solves[0]["candidates"] == [1e41]
    assert steps[0]["cone_limit"] == 1e-39
