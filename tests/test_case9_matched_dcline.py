"""Replay the matched PYPOWER points, not the unmodified DC-line oracle.

The checked configuration has a separate fixed withdrawal at bus 4 and
zero converter Q, and applies the same voltage, angle, and generator limits.
Same-point costs use 1e-4 + 1e-6 * abs(reference cost); distinct accepted
dispatches use the benchmark's 1-objective-unit materiality threshold.
"""

import json
from pathlib import Path

import cvxpy as cp
import numpy as np
import pytest

from cvxopf.problem import OPFBuild
from experiments.case9_matched_comparison.contract import (
    ANGLE_TOL_DEG,
    CELLS,
    MW_TOL,
    V_TOL,
    audit_link,
    box_excess,
    cost_close,
    generator_cost,
    link_point,
    point_suite,
    selected_links,
    solution_point,
    validate_cell,
)
from experiments.case9_matched_comparison.cvx_side import CvxModel, make_case

FIXTURE = Path(__file__).with_name("fixtures") / "case9_matched_dcline_pypower_reference.json"
pytestmark = pytest.mark.filterwarnings("ignore:hvdc_from_dcline omitted unsupported converter terms:")


@pytest.fixture(scope="module")
def matched_reference():
    data = json.loads(FIXTURE.read_text())
    assert data["schema"] == 1 and data["case"] == "case9_matched_dcline"
    assert set(data["references"]) == set(CELLS)
    assert data["units"] == {
        "active_power": "MW", "reactive_power": "MVAr", "voltage": "p.u.",
        "angle": "radians", "duration_hours": 1, "objective": "cost units over one hour",
    }
    return data


def matched_model(cell):
    case = make_case(cell)
    validate_cell(cell, case)
    model = CvxModel(case)
    withdrawal = np.zeros((len(case["bus"]), 2))
    if selected_links(case):
        withdrawal[model.build.data["ext_to_int"][4], 0] = 1.0
    # No ordinary demand was changed; only the named fixed withdrawal was added.
    np.testing.assert_allclose(model.load_by_bus, case["bus"][:, 2:4] + withdrawal, rtol=0, atol=1e-10)
    assert not model.build.data["load_is_sheddable"].any()
    return model


def replay(model, point):
    report = model.evaluate(point, numerical=True)
    for family in ("p_mw", "q_mvar"):
        # Check every external bus, not just a signed or system-wide sum.
        np.testing.assert_allclose(report[family], np.zeros(len(point["bus_ids"])), rtol=0, atol=MW_TOL)
    tolerances = {
        "real_balance": MW_TOL, "reactive_balance": MW_TOL,
        "voltage_box": V_TOL, "angle_box": ANGLE_TOL_DEG, "reference_angle": ANGLE_TOL_DEG,
        "generator_p_box": MW_TOL, "generator_q_box": MW_TOL, "capability": MW_TOL,
    }
    if point["links"]:
        tolerances.update(link_from_box=MW_TOL, link_proportional=MW_TOL)
    assert report["worst"].keys() == tolerances.keys()
    for family, entry in report["worst"].items():
        assert abs(entry["signed"]) <= tolerances[family], (family, entry)
    for (row, dc), link in zip(selected_links(model.case).items(), point["links"], strict=True):
        audit_link(row, dc, link)
        assert link["net_from_mw"] <= MW_TOL
        assert link["physical_loss_mw"] == pytest.approx(
            -link["native_from_mw"] - link["native_to_mw"] + link["fixed_withdrawal_mw"], abs=MW_TOL,
        )
    if point["links"]:
        converter = point["links"][0]
        assert (converter["row"], converter["from_bus"], converter["to_bus"]) == (0, 30, 4)
        assert converter["fixed_withdrawal_mw"] == 1.0
        assert converter["net_to_mw"] == pytest.approx(converter["native_to_mw"] - 1.0, abs=MW_TOL)
    return report


def check_returned_primal(model):
    # Inspect the untouched solver values. Physical replay below reconstructs
    # the lifted flows and would erase inconsistencies in those variables.
    core, angles, capability = model._final_rows()
    rad_tol = np.deg2rad(ANGLE_TOL_DEG)
    pu_tol = MW_TOL / model.case["baseMVA"]
    groups = (
        ("reference angle (radians)", core[:1], rad_tol),
        ("flows, balance and generator bounds (p.u.)", core[1:15], pu_tol),
        ("link limits and loss (MW)", core[15:], MW_TOL),
        ("branch angles (radians)", angles, rad_tol),
        ("generator capability (MW/MVAr projection)", capability, MW_TOL),
    )
    for name, rows, tolerance in groups:
        for i, row in enumerate(rows):
            np.testing.assert_allclose(row.violation(), 0., rtol=0, atol=tolerance,
                                       err_msg=f"returned primal: {name}, row {i}")
    voltage = model.variables["v"]
    np.testing.assert_allclose(box_excess(*voltage.bounds, voltage.value), 0.,
                               rtol=0, atol=V_TOL, err_msg="returned primal: voltage bounds (p.u.)")


def assert_passive_five_to_nine(link):
    assert (link["from_bus"], link["to_bus"]) == (5, 9)
    sending = -link["net_from_mw"]
    assert sending >= -MW_TOL and link["net_to_mw"] >= -MW_TOL
    assert link["net_to_mw"] == pytest.approx(0.95 * sending, abs=MW_TOL)
    assert link["physical_loss_mw"] >= -MW_TOL
    assert link["physical_loss_mw"] == pytest.approx(0.05 * sending, abs=MW_TOL)


@pytest.mark.parametrize("cell", CELLS)
def test_replays_matched_pypower_dispatch_without_solving(matched_reference, cell, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("matched reference replay must not solve or redispatch")

    monkeypatch.setattr(OPFBuild, "solve", forbidden)
    monkeypatch.setattr(cp.Problem, "solve", forbidden)
    ref = matched_reference["references"][cell]
    point = ref["point"]
    model = matched_model(cell)
    assert ref["generator_bus_ids"] == model.case["gen"][:, 0].tolist()
    report = replay(model, point)
    cost_close(report["objective"], ref["objective"], f"{cell} CVXOPF cost at PYPOWER dispatch")
    independently_priced = sum(generator_cost(row, p) for row, p in zip(
        model.case["gencost"], point["pg_mw"], strict=True,
    ))
    cost_close(independently_priced, ref["objective"], f"{cell} independent cost")
    if cell == "pwl_links":
        link = next(link for link in point["links"] if link["row"] == 3)
        assert_passive_five_to_nine(link)
        # This is a snapshot of the retained dispatch, not a routing
        # requirement on any fresh solution.
        assert [link["net_from_mw"], link["net_to_mw"], link["physical_loss_mw"]] == pytest.approx(
            [-10.0, 9.5, 0.5], abs=MW_TOL,
        )


def test_returned_primal_check_rejects_corrupted_lift(matched_reference):
    model = matched_model("pwl_links")
    point = matched_reference["references"]["pwl_links"]["point"]
    model.evaluate(point, numerical=True)
    check_returned_primal(model)
    lifted = model.variables["P_vec"]
    corrupted = lifted.value.copy()
    corrupted[0] += 1.
    lifted.save_value(corrupted)
    with pytest.raises(AssertionError, match="returned primal: flows"):
        check_returned_primal(model)
    replay(model, point)  # Replay alone would accept by replacing the corrupt lift.


def test_passive_loss_check_accepts_unsaturated_flow_and_rejects_gain():
    dc = selected_links(make_case("pwl_links"))[3]
    assert_passive_five_to_nine(link_point(3, dc, 9.990))
    gain = dict(link_point(3, dc, 9.5), net_to_mw=10., physical_loss_mw=-0.5)
    with pytest.raises(AssertionError):
        assert_passive_five_to_nine(gain)


def test_solves_matched_case9_pwl_with_dclines(matched_reference):
    model = matched_model("pwl_links")
    # Use the benchmark's prescribed, unsolved physical start, not the
    # solution in the fixture.
    model.evaluate(point_suite(model.case)[0])
    settings = matched_reference["benchmark"]["settings"]
    model.build.solve(warm_start=False, **{**settings["cvxopf"], "print_level": 0})
    assert model.build.prob.status == cp.OPTIMAL
    check_returned_primal(model)
    solved_cost = model.build.prob.value
    leaves = {name: var.value for name, var in model.variables.items()}
    base = model.case["baseMVA"]
    order = [model.build.data["ext_to_int"][int(bus)] for bus in model.case["bus"][:, 0]]
    point = solution_point(
        model.case, leaves["theta"][order], leaves["v"][order], base * leaves["Pg"], base * leaves["Qg"],
        zip(leaves["p_hvdc_in"], leaves["p_hvdc_out"], strict=True),
    )
    report = replay(model, point)
    cost_close(report["objective"], solved_cost, "replayed CVXOPF objective")
    assert solved_cost == pytest.approx(
        matched_reference["references"]["pwl_links"]["objective"],
        abs=settings["material_cost_threshold"], rel=0,
    )
    assert_passive_five_to_nine(next(link for link in point["links"] if link["row"] == 3))
