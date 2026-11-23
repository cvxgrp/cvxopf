"""Cheap stop/resume rehearsals and opt-in pinned callback integration."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import cvxpy as cp
import pytest

from cvxopf import hvdc

from . import preflight as runner
from .contract import ACTIVE_LINK_ROWS, CELLS, point_suite
from .cvx_side import CvxModel, make_case


@pytest.fixture
def synthetic(monkeypatch):
    calls = []
    state = {"binding": "original"}

    def fake_check(cell, attempt, python):
        calls.append(cell)
        for name in runner.ARTIFACTS:
            (attempt / name).write_text("synthetic work only\n")
        if state.get("interrupt") == cell:
            raise KeyboardInterrupt
        return {"cell": cell, "no_solver_iterations": True, "point_comparisons": []}

    monkeypatch.setattr(runner, "provenance", lambda _: {"synthetic": state["binding"]})
    monkeypatch.setattr(runner, "cell_check", fake_check)
    return state, calls


def test_resume_retains_interrupted_attempt(tmp_path, synthetic):
    run = tmp_path / "interrupted"
    state, calls = synthetic
    python = Path(sys.executable)
    state["interrupt"] = CELLS[1]
    with pytest.raises(KeyboardInterrupt):
        runner.run(run, python, resume=False)
    assert calls == list(CELLS[:2])
    assert runner.cell_receipt(run, CELLS[0]) is not None
    assert runner.cell_receipt(run, CELLS[1]) is None
    (old_attempt,) = [p.parent for p in runner.attempts_for(run, CELLS[1])]
    assert runner.load(old_attempt / "failure.json")["type"] == "KeyboardInterrupt"

    state.pop("interrupt")
    runner.run(run, python, resume=True)
    assert calls == [*CELLS[:2], *CELLS[1:]]
    assert all(runner.cell_receipt(run, cell) for cell in CELLS)
    assert len(runner.attempts_for(run, CELLS[1])) == 2
    assert (old_attempt / "failure.json").is_file()


def test_resume_publishes_finished_receipt_without_rechecking(tmp_path, synthetic, monkeypatch):
    run = tmp_path / "publication_gap"
    _, calls = synthetic
    python = Path(sys.executable)
    publish = runner.publish

    def stop_before_publish(*_):
        raise KeyboardInterrupt

    with monkeypatch.context() as mock:
        mock.setattr(runner, "publish", stop_before_publish)
        with pytest.raises(KeyboardInterrupt):
            runner.run(run, python, resume=False)
    assert runner.publish is publish
    assert calls == [CELLS[0]]
    assert runner.cell_receipt(run, CELLS[0]) is None
    runner.run(run, python, resume=True)
    assert calls == list(CELLS)
    assert len(runner.attempts_for(run, CELLS[0])) == 1


def test_changed_binding_and_corrupt_published_artifact_stop_resume(tmp_path, synthetic):
    run = tmp_path / "corrupt"
    state, calls = synthetic
    python = Path(sys.executable)
    runner.run(run, python, resume=False)
    state["binding"] = "changed"
    with pytest.raises(ValueError, match="bound preflight inputs changed"):
        runner.run(run, python, resume=True)
    state["binding"] = "original"
    (started,) = runner.attempts_for(run, CELLS[0])
    (started.parent / "pypower.stdout").write_text("damaged\n")
    with pytest.raises(ValueError, match="published attempt hash mismatch"):
        runner.run(run, python, resume=True)
    assert calls == list(CELLS)


def test_binding_covers_case9_execution_not_unrelated_code():
    names = {path.name for path in runner.bound_files()}
    assert {"case9.py", "case9_dcline.py", "hvdc.py", "pypower_side.py"} <= names
    assert not {"case118.py", "storage_lossy.py", "test_preflight.py", "README.md"} & names


@pytest.mark.parametrize(("supplied", "reason"), [
    ("smooth_no_links", "smooth_links active DC link rows"),
    ("pwl_links", "smooth_links generator cost rows"),
])
def test_wrong_case_cannot_pass_under_smooth_links_label(tmp_path, monkeypatch, supplied, reason):
    case = make_case(supplied)
    monkeypatch.setattr(runner, "make_case", lambda _: case)
    with pytest.raises(ValueError, match=reason):
        runner.cell_check("smooth_links", tmp_path / "mislabeled", Path(sys.executable))
    assert not (tmp_path / "mislabeled").exists()


@pytest.mark.parametrize(("fault", "reason"), [
    ("drop_study_rows", "CVXOPF final problem missing or has extra constraint rows"),
    ("one_sided_loss", "CVXOPF final link loss row must be a per-link equality"),
])
def test_cvxopf_rejects_faults_in_final_problem(fault, reason):
    case = make_case("pwl_links")
    with pytest.warns(UserWarning, match="omitted unsupported converter terms"):
        cvx = CvxModel(case)
    prob = cvx.build.prob
    rows = prob.constraints
    if fault == "drop_study_rows":
        rows = rows[:-8]
    else:
        lhs, rhs = rows[17].args
        rows[17] = lhs <= rhs
    cvx.build = replace(cvx.build, prob=cp.Problem(prob.objective, rows))
    with pytest.raises(ValueError, match=reason):
        cvx.evaluate(point_suite(case)[0])


def test_cvxopf_uses_final_angle_rows_even_when_inventory_is_unchanged():
    case = make_case("smooth_no_links")
    cvx = CvxModel(case)
    prob = cvx.build.prob
    point = point_suite(case)[0]
    correct = cvx.evaluate(point)
    rows = prob.constraints
    lhs, rhs = rows[15].args
    rows[15] = lhs <= rhs + 1.
    cvx.build = replace(cvx.build, prob=cp.Problem(prob.objective, rows))
    with pytest.raises(ValueError, match="CVXOPF complete angle bounds"):
        runner.compare_point(cvx, correct, case, point)


def test_cvxopf_loss_probes_detect_cross_link_coupling():
    case = make_case("pwl_links")
    with pytest.warns(UserWarning, match="omitted unsupported converter terms"):
        cvx = CvxModel(case)
    prob = cvx.build.prob
    rows = prob.constraints
    residual = rows[17].expr
    rows[17] = cp.hstack([residual[0], residual[1] + residual[0], residual[2]]) == 0.
    cvx.build = replace(cvx.build, prob=cp.Problem(prob.objective, rows))
    point = point_suite(case)[0]
    cvx.evaluate(point)  # On the original equality surface, all rows are zero.
    with pytest.raises(ValueError, match="CVXOPF link 0 receiving-terminal probe"):
        cvx.probe_link_losses(point)


@pytest.fixture
def pinned_python():
    python = os.environ.get("CASE9_PINNED_PYPOWER_PYTHON")
    if not python:
        pytest.skip("set CASE9_PINNED_PYPOWER_PYTHON to run the pinned PYPOWER callback checks")
    return Path(python).absolute()


@pytest.mark.parametrize("cell", CELLS)
def test_live_assembled_non_solving_gates(tmp_path, cell, pinned_python):
    report = runner.cell_check(cell, tmp_path / cell, pinned_python)
    assert report["no_solver_iterations"] is True
    assert report["callback_gate"]["nonlinear_h"] == [18]
    assert report["callback_gate"]["hessian"][0] == report["callback_gate"]["hessian"][1]
    assert all(row["margin_mva"] > 0 for row in report["rating_proof"])
    links = ACTIVE_LINK_ROWS if "dcline" in make_case(cell) else ()
    assert len(report["negative_controls"]) == (13 if links else 10)
    probes = report["cvxopf_link_loss_probes"]
    assert [(p["source_row"], p["receiving_delta_mw"]) for p in probes] == [
        (row, delta) for row in links for delta in (-1., 1.)
    ]
    for probe in probes:
        assert probe["violation_mw"] == pytest.approx([
            float(row == probe["source_row"]) for row in links
        ])
    if cell == "pwl_links":
        cstar = report["historical_controls"]["source_replay"]["cstar"]
        assert cstar["bus9_mw"] == pytest.approx(-23)


def test_unequal_transfers_detect_swapped_from_bus_columns(tmp_path, pinned_python, monkeypatch):
    case = make_case("pwl_links")
    report = runner.cell_check("pwl_links", tmp_path / "link_swap", pinned_python)
    comparisons = {row["point"]["name"]: row for row in report["point_comparisons"]}
    point = comparisons["unequal_bus9_links"]["point"]
    assert [link["net_from_mw"] for link in point["links"][1:]] == [-2, -8]
    assert sum(link["net_to_mw"] for link in point["links"][1:]) == pytest.approx(9.6)
    make_incidence = hvdc._make_hvdc_incidence_matrices

    def swap_from_bus_columns(*args, **kwargs):
        from_bus, to_bus = make_incidence(*args, **kwargs)
        return from_bus[:, [0, 2, 1]], to_bus

    monkeypatch.setattr(hvdc, "_make_hvdc_incidence_matrices", swap_from_bus_columns)
    with pytest.warns(UserWarning, match="omitted unsupported converter terms"):
        wrong_model = CvxModel(case)
    equal = comparisons["accounting"]
    runner.compare_point(wrong_model, equal["pypower"], case, equal["point"])
    with pytest.raises(ValueError, match="CVXOPF nodal P sign/units"):
        runner.compare_point(wrong_model, comparisons["unequal_bus9_links"]["pypower"], case, point)


def test_pypower_rejects_lowered_pwl_floor_and_shifted_historical_reference(tmp_path, pinned_python):
    case = make_case("pwl_links")
    attempt = tmp_path / "pypower_faults"
    runner.cell_check("pwl_links", attempt, pinned_python)
    probe = """
import json
import sys
from pathlib import Path

from experiments.case9_matched_comparison.contract import decode_case
from experiments.case9_matched_comparison.pypower_side import PypModel, replay_history

request = json.loads(Path(sys.argv[1]).read_text())
case = decode_case(request["case"])
model = PypModel(case, [r["rating_mva"] for r in request["ratings"]])
point = request["points"][0]
model.evaluate(point)
rows = model.indices(model.ll, "ycon")
coeff = model.A[rows, :][:, model.cols("y")].toarray()[:, 0]
model.upper[rows] += 1000 * abs(coeff)
try:
    model.evaluate(point)
except ValueError as exc:
    reason = str(exc)
else:
    raise RuntimeError("lowered floor was admitted")
history = request["historical"]
pstar = history["pstar_full.json"]
pstar["Va_deg"] = [a + 5 for a in pstar["Va_deg"]]
print(json.dumps({"floor_rejection": reason, "historical": replay_history(case, history)}))
"""
    result = subprocess.check_output(
        [str(pinned_python), "-c", probe, str(attempt / "request.json")],
        cwd=runner.REPO, env=runner.child_env(), timeout=30,
    )
    evidence = json.loads(result)
    assert "PYP minimum PWL cost for generator row 0" in evidence["floor_rejection"]
    replay = evidence["historical"]["pstar"]
    assert replay["largest_angle_deg_violation"] == pytest.approx(0)
    assert replay["reference_angle_deg_violation"] == pytest.approx(5)
    with pytest.raises(ValueError, match="P\\* historical target reference_angle_deg_violation"):
        runner.historical_controls(case, runner.historical_inputs(), evidence["historical"])
