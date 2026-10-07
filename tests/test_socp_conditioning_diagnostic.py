"""No numerical solves: scaling algebra, restoration, and evidence contracts."""

from copy import deepcopy
import json
from types import SimpleNamespace

import cvxpy as cp
import numpy as np
import pytest
from scipy import sparse

from cvxopf import nondispatchable, storage
from experiments.socp_conditioning import diagnostic as d


@pytest.mark.parametrize(
    "p,q,feasible",
    [
        (0.0, 0.0, True),
        (1.0, 0.0, True),
        (-1.0, 0.0, True),
        (0.6, 0.8, True),
        (0.61, 0.8, False),
        (0.0, -1.01, False),
    ],
)
def test_unit_cones_have_original_circle_feasible_set(p, q, feasible):
    ratings = np.array([0.1, 17.0, 1700.0])
    pvar, qvar = cp.Variable((3, 2)), cp.Variable((3, 2))
    pvar.value = np.broadcast_to(p * ratings[:, None], (3, 2))
    qvar.value = np.broadcast_to(q * ratings[:, None], (3, 2))
    constraint = d.unit_cones(pvar, qvar, ratings)
    assert constraint.is_dcp()
    assert bool(np.max(constraint.violation()) < 1e-10) == feasible
    original = pvar.value**2 + qvar.value**2 - ratings[:, None] ** 2
    assert bool(np.max(original) <= 1e-8) == feasible


@pytest.mark.parametrize("ratings", [[1], [1, -1], [1, float("nan")]])
def test_invalid_ratings_rejected(ratings):
    with pytest.raises(ValueError):
        d.unit_cones(cp.Variable((2, 3)), cp.Variable((2, 3)), ratings)


@pytest.mark.parametrize("variant", d.VARIANTS)
def test_hooks_restore_and_cost_units_preserved(variant):
    nd, st = (
        nondispatchable.vectorized_ac_operating_constraints,
        storage.vectorized_ac_operating_constraints,
    )
    build, original, divisor = d.build_variant(d.smoke_inputs(), variant)
    assert nondispatchable.vectorized_ac_operating_constraints is nd
    assert storage.vectorized_ac_operating_constraints is st
    for var in build.prob.variables():
        var.save_value(np.ones(var.shape))
    assert build.prob.is_dcp()
    assert float(build.prob.objective.value) * divisor == pytest.approx(
        float(original.value)
    )
    assert sum(
        float(e.value) for k, e in build.expressions.items() if k.endswith("_cost")
    ) == pytest.approx(float(original.value))
    assert divisor == (1e6 if variant in ("objective", "combined") else 1.0)


def test_hooks_restore_on_construction_exception(monkeypatch):
    nd, st = (
        nondispatchable.vectorized_ac_operating_constraints,
        storage.vectorized_ac_operating_constraints,
    )

    def fail(**kwargs):
        raise RuntimeError("construction failed")

    monkeypatch.setattr(d, "build_opf_multistep", fail)
    with pytest.raises(RuntimeError):
        d.build_variant(d.smoke_inputs(), "combined")
    assert nondispatchable.vectorized_ac_operating_constraints is nd
    assert storage.vectorized_ac_operating_constraints is st


def test_no_ac_patch_or_unknown_variant():
    kwargs = d.smoke_inputs()
    kwargs["formulation"] = "ac"
    with pytest.raises(ValueError):
        d.build_variant(kwargs, "cones")
    with pytest.raises(ValueError):
        d.build_variant(d.smoke_inputs(), "other")


def test_immutable_strict_publication(tmp_path):
    path = tmp_path / "result.json"
    d.publish(path, {"unavailable": float("nan"), "data": np.array([1.0, 2.0])})
    assert json.loads(path.read_text()) == {"unavailable": None, "data": [1.0, 2.0]}
    with pytest.raises(FileExistsError):
        d.publish(path, {})


def test_complete_native_evidence_and_offset_units():
    data = dict(
        A=sparse.csc_matrix([[1.0, 0.0], [0.0, 1.0]]),
        P=sparse.eye(2, format="csc"),
        c=np.array([-1.0, -2.0]),
        b=np.array([1.0, 2.0]),
        dims=SimpleNamespace(zero=2),
    )
    native = dict(
        x=[1.0, 2.0], s=[0.0, 0.0], z=[0.0, 0.0], obj_val=-2.5, obj_val_dual=-2.5
    )
    result = d.canonical_evidence(data, [{"offset": 3.0}], native, 5.0, 10.0)
    assert result["primal_inf"] == result["dual_inf"] == 0
    assert result["native_objective_reconstruction_error"] == 0
    assert result["native_vs_original_objective"] == 0
    assert result["native_primal_original_units"] == 5


def test_success_requires_all_gates():
    record = dict(
        exception=None,
        native_solution=dict(status="Solved"),
        result=dict(objective=10.0),
        audit=dict(passed=True),
        canonical_evidence=dict(native_vs_original_objective=0.0),
    )
    assert d.successful(record)
    for key, value in [
        ("exception", "solver failed"),
        ("native_solution", dict(status="AlmostSolved")),
        ("audit", dict(passed=False)),
        ("result", dict(objective=float("nan"))),
        ("canonical_evidence", dict(native_vs_original_objective=1.0)),
    ]:
        bad = deepcopy(record)
        bad[key] = value
        assert not d.successful(bad)


def test_solver_exception_retained_before_audit(tmp_path, monkeypatch):
    build, original, divisor = d.build_variant(d.smoke_inputs(), "baseline")

    def fail(**kwargs):
        raise RuntimeError("actual solve path failure")

    monkeypatch.setattr(build, "solve", fail)
    monkeypatch.setattr(d, "build_variant", lambda *a: (build, original, divisor))
    monkeypatch.setattr(d, "context", lambda: {})
    monkeypatch.setattr(
        d, "original_audit", lambda *a: pytest.fail("must not audit missing primal")
    )
    d.worker(tmp_path, "case9", "baseline", tmp_path)
    import gzip

    with gzip.open(tmp_path / "arm.json.gz", "rt") as stream:
        record = json.load(stream)
    assert "actual solve path failure" in record["exception"]
    assert not record["accepted"]
    assert record["audit"] is None
    assert (tmp_path / "completion.json").exists()


def test_unrecognized_smoke_summary_cannot_authorize_continuation(tmp_path):
    (tmp_path / "summary.json").write_text("{}")
    with pytest.raises(ValueError, match="unexpected prior smoke summary"):
        d.verified_smoke_reference(tmp_path)


def test_reviewed_continuation_keeps_order_and_failed_smoke_label(
    tmp_path, monkeypatch
):
    calls = []
    prior = dict(original_smoke_gate_passed=False)
    monkeypatch.setattr(d, "verified_smoke_reference", lambda p: prior)
    monkeypatch.setattr(d, "rss", lambda p: 100.0)
    monkeypatch.setattr(d, "context", lambda: {})

    def fake_supervise(root, case, variant, reference):
        calls.append((case, variant))
        return dict(case=case, variant=variant, accepted=variant == "cones")

    monkeypatch.setattr(d, "supervise", fake_supervise)
    out = tmp_path / "run"
    d.run(out, tmp_path, tmp_path / "smoke")
    assert calls == [("surplus", v) for v in d.VARIANTS] + [
        ("deficit", "baseline"),
        ("deficit", "cones"),
    ]
    result = json.loads((out / "summary.json").read_text())
    assert result["complete"]
    assert result["reviewed_smoke_reference"]["original_smoke_gate_passed"] is False


def test_unsuccessful_surplus_does_not_launch_deficit(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        d, "verified_smoke_reference", lambda p: dict(original_smoke_gate_passed=False)
    )
    monkeypatch.setattr(d, "rss", lambda p: 100.0)
    monkeypatch.setattr(d, "context", lambda: {})

    def fake_supervise(root, case, variant, reference):
        calls.append((case, variant))
        return dict(case=case, variant=variant, accepted=False)

    monkeypatch.setattr(d, "supervise", fake_supervise)
    d.run(tmp_path / "run", tmp_path, tmp_path / "smoke")
    assert calls == [("surplus", v) for v in d.VARIANTS]


@pytest.mark.parametrize("divisor", d.SWEEP_DIVISORS)
def test_sweep_scales_entire_objective_only(divisor):
    build, original, actual = d.build_variant(d.smoke_inputs(), "combined", divisor)
    for var in build.prob.variables():
        var.save_value(np.ones(var.shape))
    assert actual == divisor
    assert build.prob.objective.value * divisor == pytest.approx(original.value)
    assert all(c.is_dcp() for c in build.prob.constraints)


@pytest.mark.parametrize(
    "variant,divisor",
    [
        ("baseline", 10.0),
        ("combined", 0.0),
        ("combined", 1e6),
        ("combined", float("nan")),
        ("combined", True),
    ],
)
def test_sweep_rejects_undeclared_factors(variant, divisor):
    with pytest.raises(ValueError, match="frozen"):
        d.build_variant(d.smoke_inputs(), variant, divisor)


def test_sweep_serial_order_no_automatic_followup(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        d, "verified_smoke_reference", lambda p: dict(original_smoke_gate_passed=False)
    )
    monkeypatch.setattr(d, "rss", lambda p: 100.0)
    monkeypatch.setattr(d, "context", lambda: {})

    def fake_supervise(root, case, variant, reference, divisor):
        calls.append((case, variant, divisor))
        return dict(accepted=False, objective_divisor=divisor)

    monkeypatch.setattr(d, "supervise", fake_supervise)
    out = tmp_path / "run"
    d.run_sweep(out, tmp_path, tmp_path / "smoke")
    assert calls == [("surplus", "combined", x) for x in d.SWEEP_DIVISORS]
    result = json.loads((out / "summary.json").read_text())
    assert result["complete"] and len(result["arms"]) == 4
    assert result["reviewed_smoke_reference"]["original_smoke_gate_passed"] is False
