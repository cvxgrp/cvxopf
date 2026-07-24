"""
Test suite for Milestone 7 HVDC transmission link model.

Gate 1 (TestHVDCUnit): pure logic — validation, incidence, static box,
  hvdc_from_dcline, hvdc_cost_expr DCP check, ac_ delegates to dc_.
Gate 2 (TestHVDCCVXPY): CVXPY component methods — box shapes, loss branches,
  mixed batches, Convention B sign check. No live solve in either gate.
Gate 3 (TestHVDCWiring): silent-ignore half — singlenode builds with hvdc=
  accepted and dropped silently; "n_hvdc" never appears in build.data.
  Positive-wiring half (TestHVDCLossyDCWiring) — lossy_dc builds expose
  "n_hvdc" in build.data and HVDC variables in build.variables.
Gate 4 (TestHVDCLossyDCSolve): live solve — case9, bus 4 → bus 9, verifying
  optimality, flow conservation, and loss relationship.
"""

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import cvxpy as cp
from cvxopf.generator import DispatchableGenerator

from cvxopf.hvdc import (
    HVDCLink,
    _validate_hvdc,
    _make_hvdc_incidence_matrices,
    _hvdc_static_box,
    hvdc_injections,
    dc_operating_constraints,
    ac_operating_constraints,
    hvdc_cost_expr,
    hvdc_from_dcline,
)
from cvxopf.testcases.case9_dcline import case9_dcline
from cvxopf.testcases.case9 import case9
from cvxopf.problem import build_opf, build_opf_multistep
from cvxopf.results import extract_results
from cvxopf.testcases import make_singlenode_case


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _link(
    from_bus=1,
    to_bus=2,
    p_min_mw=-100.0,
    p_max_mw=100.0,
    loss_percent=0.0,
    cost_coeffs=(0.0, 0.0, 0.0),
):
    return HVDCLink(
        from_bus=from_bus,
        to_bus=to_bus,
        p_min_mw=p_min_mw,
        p_max_mw=p_max_mw,
        loss_percent=loss_percent,
        cost_coeffs=cost_coeffs,
    )


# Simple 3-bus ext_to_int for offline tests (buses 1,2,3 -> 0,1,2)
_EXT_TO_INT = {1: 0, 2: 1, 3: 2}
_EXT_BUS_IDS = {1, 2, 3}
_NB = 3


# ===========================================================================
# Gate 1 — pure logic
# ===========================================================================


class TestHVDCValidation:
    def test_happy_path_passes(self):
        _validate_hvdc([_link()], _EXT_BUS_IDS)  # no exception

    def test_empty_list_passes(self):
        _validate_hvdc([], _EXT_BUS_IDS)

    def test_from_eq_to_raises(self):
        with pytest.raises(ValueError, match="from_bus and to_bus must differ"):
            _validate_hvdc([_link(from_bus=1, to_bus=1)], _EXT_BUS_IDS)

    def test_from_bus_not_in_case(self):
        with pytest.raises(ValueError, match="from_bus 99"):
            _validate_hvdc([_link(from_bus=99)], _EXT_BUS_IDS)

    def test_to_bus_not_in_case(self):
        with pytest.raises(ValueError, match="to_bus 99"):
            _validate_hvdc([_link(to_bus=99)], _EXT_BUS_IDS)

    def test_p_max_zero_raises(self):
        with pytest.raises(ValueError, match="p_max_mw must be > 0"):
            _validate_hvdc([_link(p_max_mw=0.0)], _EXT_BUS_IDS)

    def test_p_max_negative_raises(self):
        with pytest.raises(ValueError, match="p_max_mw must be > 0"):
            _validate_hvdc([_link(p_max_mw=-10.0)], _EXT_BUS_IDS)

    def test_p_min_gt_p_max_raises(self):
        with pytest.raises(ValueError, match="p_min_mw"):
            _validate_hvdc([_link(p_min_mw=200.0, p_max_mw=100.0)], _EXT_BUS_IDS)

    def test_p_min_eq_p_max_allowed(self):
        # degenerate box is allowed; coincident bounds pin p_in
        _validate_hvdc([_link(p_min_mw=50.0, p_max_mw=50.0)], _EXT_BUS_IDS)

    def test_loss_negative_raises(self):
        with pytest.raises(ValueError, match="loss_percent must be >= 0"):
            _validate_hvdc([_link(loss_percent=-1.0)], _EXT_BUS_IDS)

    def test_c2_negative_raises(self):
        with pytest.raises(ValueError, match="c2 must be >= 0"):
            _validate_hvdc([_link(cost_coeffs=(0.0, 0.0, -1.0))], _EXT_BUS_IDS)

    def test_c1_negative_raises(self):
        with pytest.raises(ValueError, match="c1 must be >= 0"):
            _validate_hvdc([_link(cost_coeffs=(0.0, -1.0, 0.0))], _EXT_BUS_IDS)

    def test_error_message_includes_index(self):
        bad = [_link(), _link(from_bus=1, to_bus=1)]
        with pytest.raises(ValueError, match="link 1"):
            _validate_hvdc(bad, _EXT_BUS_IDS)


class TestHVDCIncidence:
    def test_empty_links_returns_empty_pair(self):
        Ch_from, Ch_to = _make_hvdc_incidence_matrices([], _NB, _EXT_TO_INT)
        assert Ch_from.shape == (_NB, 0)
        assert Ch_to.shape == (_NB, 0)

    def test_single_link_shapes(self):
        lnk = _link(from_bus=1, to_bus=3)
        Ch_from, Ch_to = _make_hvdc_incidence_matrices([lnk], _NB, _EXT_TO_INT)
        assert Ch_from.shape == (_NB, 1)
        assert Ch_to.shape == (_NB, 1)

    def test_from_bus_entry(self):
        lnk = _link(from_bus=1, to_bus=3)
        Ch_from, _ = _make_hvdc_incidence_matrices([lnk], _NB, _EXT_TO_INT)
        # bus 1 -> internal 0
        assert Ch_from[0, 0] == 1.0
        assert Ch_from[1, 0] == 0.0
        assert Ch_from[2, 0] == 0.0

    def test_to_bus_entry(self):
        lnk = _link(from_bus=1, to_bus=3)
        _, Ch_to = _make_hvdc_incidence_matrices([lnk], _NB, _EXT_TO_INT)
        # bus 3 -> internal 2
        assert Ch_to[2, 0] == 1.0
        assert Ch_to[0, 0] == 0.0

    def test_two_links(self):
        links = [_link(from_bus=1, to_bus=2), _link(from_bus=2, to_bus=3)]
        Ch_from, Ch_to = _make_hvdc_incidence_matrices(links, _NB, _EXT_TO_INT)
        assert Ch_from.shape == (_NB, 2)
        assert Ch_from[0, 0] == 1.0  # link 0 from_bus=1 -> int 0
        assert Ch_from[1, 1] == 1.0  # link 1 from_bus=2 -> int 1
        assert Ch_to[1, 0] == 1.0  # link 0 to_bus=2 -> int 1
        assert Ch_to[2, 1] == 1.0  # link 1 to_bus=3 -> int 2


class TestHVDCStaticBox:
    def test_single_link_reads_fields(self):
        lnk = HVDCLink(from_bus=1, to_bus=2, p_min_mw=-30.0, p_max_mw=100.0)
        p_min, p_max = _hvdc_static_box([lnk])
        assert p_min[0] == pytest.approx(-30.0)
        assert p_max[0] == pytest.approx(100.0)

    def test_degenerate_box_read_correctly(self):
        lnk = HVDCLink(from_bus=1, to_bus=2, p_min_mw=40.0, p_max_mw=40.0)
        p_min, p_max = _hvdc_static_box([lnk])
        assert p_min[0] == pytest.approx(40.0)
        assert p_max[0] == pytest.approx(40.0)

    def test_two_links_vectorized(self):
        links = [
            HVDCLink(from_bus=1, to_bus=2, p_min_mw=0.0, p_max_mw=50.0),
            HVDCLink(from_bus=2, to_bus=3, p_min_mw=-100.0, p_max_mw=100.0),
        ]
        p_min, p_max = _hvdc_static_box(links)
        assert p_min.shape == (2,)
        assert p_max.shape == (2,)
        assert p_min[0] == pytest.approx(0.0)
        assert p_max[0] == pytest.approx(50.0)
        assert p_min[1] == pytest.approx(-100.0)
        assert p_max[1] == pytest.approx(100.0)

    def test_empty_list_returns_empty_arrays(self):
        p_min, p_max = _hvdc_static_box([])
        assert p_min.shape == (0,)
        assert p_max.shape == (0,)


class TestHVDCFromDcline:
    def _case_tables(self):
        ppc = case9_dcline()
        return ppc["dcline"], ppc["dclinecost"]

    def test_inactive_row_skipped(self):
        # case9_dcline has 4 rows; row index 2 has status=0 (verify below)
        dcline, dclinecost = self._case_tables()
        links = hvdc_from_dcline(dcline, dclinecost)
        # 4 rows minus 1 inactive = 3 links
        assert len(links) == 3

    def test_loss0_warning_emitted(self):
        # row 0 of t_case9_dcline has loss0=1 (nonzero)
        dcline, dclinecost = self._case_tables()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            hvdc_from_dcline(dcline, dclinecost)
        msgs = [str(x.message) for x in w if issubclass(x.category, UserWarning)]
        assert any("loss0" in m.lower() or "fixed converter" in m.lower() for m in msgs)

    def test_no_warning_when_loss0_zero(self):
        # Build a minimal table with no loss0
        dcline = np.array(
            [[1, 2, 1, 10.0, 9.0, 0, 0, 1, 1, 0, 20, 0, 0, 0, 0, 0.0, 0.0]]
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            hvdc_from_dcline(dcline)
        assert not any(issubclass(x.category, UserWarning) for x in w)

    def test_pmin_pmax_mapped(self):
        dcline, dclinecost = self._case_tables()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            links = hvdc_from_dcline(dcline, dclinecost)
        for lnk in links:
            assert lnk.p_min_mw <= lnk.p_max_mw
            assert lnk.p_max_mw > 0

    def test_loss_percent_from_loss1(self):
        # row 0: loss1=0.01 -> loss_percent=1.0 (row is active, loss0=1 -> warning)
        dcline, dclinecost = self._case_tables()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            links = hvdc_from_dcline(dcline, dclinecost)
        # row 0 is active, loss1=0.01
        assert links[0].loss_percent == pytest.approx(1.0)

    def test_quadratic_dclinecost_reversal(self):
        # Synthetic quadratic row: [2, 0, 0, 3, 5.0, 3.0, 1.0]
        # highest-first coeffs: [5.0, 3.0, 1.0] -> lowest-first: (1.0, 3.0, 5.0)
        # c2=5.0 (quadratic), c1=3.0 (linear), c0=1.0 (constant)
        # Distinct nonzero values ensure a swap fails the assertion.
        dcline = np.array(
            [[1, 2, 1, 10.0, 9.0, 0, 0, 1, 1, 0, 20, 0, 0, 0, 0, 0.0, 0.01]]
        )
        cost_row = np.array([[2, 0, 0, 3, 5.0, 3.0, 1.0]])
        links = hvdc_from_dcline(dcline, cost_row)
        assert len(links) == 1
        assert links[0].cost_coeffs == pytest.approx((1.0, 3.0, 5.0))

    def test_linear_dclinecost_n2_padding(self):
        # Linear row: [2, 0, 0, 2, 7.3, 0.0] -> (c0=0.0, c1=7.3, c2=0.0)
        dcline = np.array(
            [[1, 2, 1, 10.0, 9.0, 0, 0, 1, 1, 0, 20, 0, 0, 0, 0, 0.0, 0.05]]
        )
        cost_row = np.array([[2, 0, 0, 2, 7.3, 0.0]])
        links = hvdc_from_dcline(dcline, cost_row)
        assert links[0].cost_coeffs == pytest.approx((0.0, 7.3, 0.0))

    def test_degree_gt3_raises(self):
        dcline = np.array(
            [[1, 2, 1, 10.0, 9.0, 0, 0, 1, 1, 0, 20, 0, 0, 0, 0, 0.0, 0.0]]
        )
        cost_row = np.array([[2, 0, 0, 4, 1.0, 2.0, 3.0, 4.0]])
        with pytest.raises(ValueError, match="degree 4"):
            hvdc_from_dcline(dcline, cost_row)

    def test_no_dclinecost_defaults_zero(self):
        dcline = np.array(
            [[1, 2, 1, 10.0, 9.0, 0, 0, 1, 1, 0, 20, 0, 0, 0, 0, 0.0, 0.0]]
        )
        links = hvdc_from_dcline(dcline, None)
        assert links[0].cost_coeffs == (0.0, 0.0, 0.0)


class TestHVDCCostExpr:
    def test_zero_cost_is_zero(self):
        p_in = cp.Variable((1,))
        expr = hvdc_cost_expr((0.0, 0.0, 0.0), p_in)
        # Should be a valid CVXPY expression evaluating to 0 when p_in=0
        prob = cp.Problem(cp.Minimize(expr), [p_in == 0])
        prob.solve()
        assert prob.value == pytest.approx(0.0, abs=1e-6)

    def test_quadratic_is_dcp_convex(self):
        p_in = cp.Variable((2,))
        expr = hvdc_cost_expr((1.0, 2.0, 3.0), p_in)
        assert expr.is_dcp()
        assert expr.is_convex()

    def test_cost_symmetric(self):
        # Cost should be the same for +p and -p
        p_in = cp.Variable((1,))
        expr = hvdc_cost_expr((0.0, 1.0, 0.0), p_in)
        prob_pos = cp.Problem(cp.Minimize(0), [p_in == 5.0])
        prob_pos.solve()
        p_in.value = np.array([5.0])
        val_pos = expr.value

        p_in.value = np.array([-5.0])
        val_neg = expr.value

        assert val_pos == pytest.approx(val_neg, abs=1e-8)


class TestACDelegates:
    def test_ac_returns_same_as_dc(self):
        links = [_link(from_bus=1, to_bus=2, p_min_mw=0.0, p_max_mw=100.0)]
        p_in = cp.Variable((1,))
        p_out = cp.Variable((1,))
        p_min_t = np.array([0.0])
        p_max_t = np.array([100.0])

        dc_list = dc_operating_constraints(links, p_in, p_out, p_min_t, p_max_t)
        ac_list = ac_operating_constraints(links, p_in, p_out, p_min_t, p_max_t)

        # Same number of constraints, same expressions (by string repr)
        assert len(dc_list) == len(ac_list)
        for dc_c, ac_c in zip(dc_list, ac_list):
            assert str(dc_c) == str(ac_c)


# ===========================================================================
# Gate 2 — CVXPY component method box assertions
# ===========================================================================


class TestHVDCInjections:
    def test_returns_two_tuple(self):
        links = [_link(from_bus=1, to_bus=2)]
        p_in = cp.Variable((1,))
        p_out = cp.Variable((1,))
        inj, inv_bMVA = hvdc_injections(links, p_in, p_out, _EXT_TO_INT)
        assert isinstance(inv_bMVA, cp.Parameter)
        assert hasattr(inj, "is_affine")

    def test_injection_is_cvxpy_expression(self):
        links = [_link(from_bus=1, to_bus=2)]
        p_in = cp.Variable((1,))
        p_out = cp.Variable((1,))
        inj, _ = hvdc_injections(links, p_in, p_out, _EXT_TO_INT)
        assert hasattr(inj, "is_affine")

    def test_parameter_unset(self):
        links = [_link(from_bus=1, to_bus=2)]
        p_in = cp.Variable((1,))
        p_out = cp.Variable((1,))
        _, inv_bMVA = hvdc_injections(links, p_in, p_out, _EXT_TO_INT)
        assert inv_bMVA.value is None

    def test_convention_b_sign_lossless(self):
        # For a lossless link with p_in > 0:
        #   from_bus gets +p_in/baseMVA (injects into grid)
        #   to_bus gets -p_in/baseMVA (withdraws from grid)
        baseMVA = 100.0
        links = [_link(from_bus=1, to_bus=2, loss_percent=0.0)]
        p_in = cp.Variable((1,))
        p_out = cp.Variable((1,))
        inj, inv_bMVA = hvdc_injections(links, p_in, p_out, _EXT_TO_INT)
        inv_bMVA.value = 1.0 / baseMVA

        p_min_t = np.array([-100.0])
        p_max_t = np.array([100.0])
        constrs = dc_operating_constraints(links, p_in, p_out, p_min_t, p_max_t)

        prob = cp.Problem(cp.Minimize(0), constrs + [p_in == 50.0])
        prob.solve(solver=cp.CLARABEL)
        assert prob.status in ("optimal", "optimal_inaccurate")

        inj_val = inj.value  # shape (nb=3,)
        # from_bus=1 -> internal 0: should be +50/100 = +0.5
        assert inj_val[0] == pytest.approx(+0.5, abs=1e-5)
        # to_bus=2 -> internal 1: should be -50/100 = -0.5
        assert inj_val[1] == pytest.approx(-0.5, abs=1e-5)
        # bus 3 uninvolved: 0
        assert inj_val[2] == pytest.approx(0.0, abs=1e-5)


class TestHVDCOperatingConstraints:
    def test_degenerate_box_no_extra_equality(self):
        # Degenerate box: p_min_t == p_max_t. Pin via coincident bounds only.
        links = [_link(from_bus=1, to_bus=2, p_min_mw=40.0, p_max_mw=40.0)]
        p_in = cp.Variable((1,))
        p_out = cp.Variable((1,))
        p_min_t = np.array([40.0])
        p_max_t = np.array([40.0])
        constrs = dc_operating_constraints(links, p_in, p_out, p_min_t, p_max_t)

        # Must return exactly 3 items (lower bound, upper bound, loss equality)
        # — NO extra equality for "p_in == p_sched"
        assert len(constrs) == 3

        # p_in and p_out are still cp.Variable (not numpy scalars)
        assert isinstance(p_in, cp.Variable)
        assert isinstance(p_out, cp.Variable)

    def test_fixed_direction_positive_lossy_branch(self):
        # p_min_t >= 0 -> from->to branch: coeff = -(1 - loss_frac)
        loss_pct = 5.0
        links = [
            _link(
                from_bus=1,
                to_bus=2,
                loss_percent=loss_pct,
                p_min_mw=0.0,
                p_max_mw=100.0,
            )
        ]
        p_in = cp.Variable((1,))
        p_out = cp.Variable((1,))
        p_min_t = np.array([0.0])
        p_max_t = np.array([100.0])
        constrs = dc_operating_constraints(links, p_in, p_out, p_min_t, p_max_t)

        prob = cp.Problem(cp.Minimize(0), constrs + [p_in == 80.0])
        prob.solve(solver=cp.CLARABEL)
        assert prob.status in ("optimal", "optimal_inaccurate")

        expected_p_out = -(1.0 - loss_pct / 100.0) * 80.0
        assert p_out.value[0] == pytest.approx(expected_p_out, abs=1e-5)

    def test_fixed_direction_negative_lossy_branch(self):
        # p_max_t <= 0 -> to->from branch: coeff = -(1 + loss_frac)
        # Link has valid p_max_mw > 0; per-step box [p_min_t, p_max_t] is [-100, 0].
        loss_pct = 5.0
        links = [
            _link(
                from_bus=1,
                to_bus=2,
                loss_percent=loss_pct,
                p_min_mw=-100.0,
                p_max_mw=100.0,
            )
        ]
        p_in = cp.Variable((1,))
        p_out = cp.Variable((1,))
        p_min_t = np.array([-100.0])
        p_max_t = np.array([0.0])
        constrs = dc_operating_constraints(links, p_in, p_out, p_min_t, p_max_t)

        prob = cp.Problem(cp.Minimize(0), constrs + [p_in == -80.0])
        prob.solve(solver=cp.CLARABEL)
        assert prob.status in ("optimal", "optimal_inaccurate")

        expected_p_out = -(1.0 + loss_pct / 100.0) * (-80.0)  # positive
        assert p_out.value[0] == pytest.approx(expected_p_out, abs=1e-5)

    def test_zero_straddling_lossless_and_warning(self):
        # Box straddles zero with nonzero loss -> lossless fallback + UserWarning
        links = [
            _link(
                from_bus=1, to_bus=2, loss_percent=2.0, p_min_mw=-100.0, p_max_mw=100.0
            )
        ]
        p_in = cp.Variable((1,))
        p_out = cp.Variable((1,))
        p_min_t = np.array([-100.0])
        p_max_t = np.array([100.0])

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            constrs = dc_operating_constraints(links, p_in, p_out, p_min_t, p_max_t)
        assert any(issubclass(x.category, UserWarning) for x in w)

        # Lossless: p_out == -p_in
        prob = cp.Problem(cp.Minimize(0), constrs + [p_in == 60.0])
        prob.solve(solver=cp.CLARABEL)
        assert p_out.value[0] == pytest.approx(-60.0, abs=1e-5)

    def test_zero_straddling_lossless_no_warning(self):
        # Box straddles zero but loss=0 -> lossless, no UserWarning
        links = [
            _link(
                from_bus=1, to_bus=2, loss_percent=0.0, p_min_mw=-100.0, p_max_mw=100.0
            )
        ]
        p_in = cp.Variable((1,))
        p_out = cp.Variable((1,))
        p_min_t = np.array([-100.0])
        p_max_t = np.array([100.0])

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            dc_operating_constraints(links, p_in, p_out, p_min_t, p_max_t)
        assert not any(issubclass(x.category, UserWarning) for x in w)

    def test_mixed_batch_single_vector_equality(self):
        # Two links in one step: link 0 fixed-direction, link 1 straddling.
        # Both must produce a single vector equality p_out == coeff_vec * p_in.
        links = [
            _link(
                from_bus=1, to_bus=2, loss_percent=4.0, p_min_mw=0.0, p_max_mw=100.0
            ),  # fixed-direction
            _link(
                from_bus=2, to_bus=3, loss_percent=4.0, p_min_mw=-100.0, p_max_mw=100.0
            ),  # zero-straddling
        ]
        p_in = cp.Variable((2,))
        p_out = cp.Variable((2,))
        p_min_t = np.array([0.0, -100.0])
        p_max_t = np.array([100.0, 100.0])

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            constrs = dc_operating_constraints(links, p_in, p_out, p_min_t, p_max_t)

        # Exactly 3 constraint objects (lower, upper, single vector equality)
        assert len(constrs) == 3

        prob = cp.Problem(cp.Minimize(0), constrs + [p_in == np.array([50.0, -30.0])])
        prob.solve(solver=cp.CLARABEL)
        assert prob.status in ("optimal", "optimal_inaccurate")

        loss_frac = 0.04
        # link 0 fixed-direction (p_min_t>=0): coeff = -(1 - 0.04)
        assert p_out.value[0] == pytest.approx(-(1 - loss_frac) * 50.0, abs=1e-5)
        # link 1 straddling: coeff = -1 (lossless)
        assert p_out.value[1] == pytest.approx(-(-30.0), abs=1e-5)


# ---------------------------------------------------------------------------
# Gate 3 — wiring: singlenode_dc silently drops hvdc
# ---------------------------------------------------------------------------


class TestHVDCWiring:
    """Gate 3 (silent-ignore half): singlenode_dc builds accept hvdc= and drop it.

    "n_hvdc" must never appear in build.data for singlenode formulations.
    Positive-wiring (lossy_dc / ac builds expose "n_hvdc") is deferred to T4.
    """

    _GENS = [
        DispatchableGenerator(
            bus=1, p_max_mw=200.0, cost_coeffs=(0.0, 1.0, 0.01)
        ),
        DispatchableGenerator(
            bus=1, p_max_mw=200.0, cost_coeffs=(0.0, 2.0, 0.02)
        ),
    ]
    _LINK = HVDCLink(from_bus=1, to_bus=2, p_min_mw=-50.0, p_max_mw=50.0)

    def _case(self):
        return make_singlenode_case(300.0, self._GENS)

    def test_singlenode_single_step_hvdc_dropped(self):
        case = self._case()
        build = build_opf(case, formulation="singlenode_dc", hvdc=[self._LINK])
        assert "n_hvdc" not in build.data

    def test_singlenode_single_step_no_warning_from_hvdc(self):
        case = self._case()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            build_opf(case, formulation="singlenode_dc", hvdc=[self._LINK])
        hvdc_warns = [x for x in w if "hvdc" in str(x.message).lower()]
        assert len(hvdc_warns) == 0

    def test_singlenode_multistep_hvdc_dropped_with_frames(self):
        case = self._case()
        T = 3
        df_P = pd.DataFrame(np.tile([300.0], (T, 1)))
        df_Q = pd.DataFrame(np.zeros((T, 1)))
        df_min = pd.DataFrame(np.tile([-50.0], (T, 1)))
        df_max = pd.DataFrame(np.tile([50.0], (T, 1)))

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf_multistep(
                case,
                df_P,
                df_Q,
                T=T,
                formulation="singlenode_dc",
                hvdc=[self._LINK],
                df_hvdc_min=df_min,
                df_hvdc_max=df_max,
            )
        assert "n_hvdc" not in build.data

    def test_singlenode_multistep_no_hvdc_frames_emits_tile_warning(self):
        # problem.py tile-fallback fires when hvdc is not None and frames absent.
        # The singlenode builder still drops hvdc, but the warning from
        # problem.py is expected and correct.
        case = self._case()
        T = 2
        df_P = pd.DataFrame(np.tile([300.0], (T, 1)))
        df_Q = pd.DataFrame(np.zeros((T, 1)))

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            build = build_opf_multistep(
                case,
                df_P,
                df_Q,
                T=T,
                formulation="singlenode_dc",
                hvdc=[self._LINK],
            )
        assert "n_hvdc" not in build.data
        tile_warns = [
            x
            for x in w
            if issubclass(x.category, UserWarning) and "hvdc" in str(x.message).lower()
        ]
        assert len(tile_warns) == 1


# ---------------------------------------------------------------------------
# Gate 3 (positive-wiring half) — lossy_dc builds expose HVDC in build
# ---------------------------------------------------------------------------


class TestHVDCLossyDCWiring:
    """Gate 3 positive-wiring: lossy_dc build with hvdc= populates build.data
    and build.variables with HVDC keys. Detection contract: "n_hvdc" in
    build.data. Variables: p_hvdc_in / p_hvdc_out."""

    _LINK = HVDCLink(from_bus=4, to_bus=9, p_min_mw=-100.0, p_max_mw=100.0)

    def test_single_step_data_key_present(self):
        build = build_opf(case9(), formulation="lossy_dc", hvdc=[self._LINK])
        assert "n_hvdc" in build.data

    def test_single_step_n_hvdc_count(self):
        build = build_opf(case9(), formulation="lossy_dc", hvdc=[self._LINK])
        assert build.data["n_hvdc"] == 1

    def test_single_step_incidence_shapes(self):
        build = build_opf(case9(), formulation="lossy_dc", hvdc=[self._LINK])
        nb = build.data["nb"]
        assert build.data["Ch_from"].shape == (nb, 1)
        assert build.data["Ch_to"].shape == (nb, 1)

    def test_single_step_variables_present(self):
        build = build_opf(case9(), formulation="lossy_dc", hvdc=[self._LINK])
        assert "p_hvdc_in" in build.variables
        assert "p_hvdc_out" in build.variables

    def test_single_step_variable_shapes(self):
        build = build_opf(case9(), formulation="lossy_dc", hvdc=[self._LINK])
        assert build.variables["p_hvdc_in"].shape == (1,)
        assert build.variables["p_hvdc_out"].shape == (1,)

    def test_no_hvdc_key_absent_without_hvdc(self):
        build = build_opf(case9(), formulation="lossy_dc")
        assert "n_hvdc" not in build.data

    def test_multistep_data_key_present(self):
        case = case9()
        T = 2
        nb = case["bus"].shape[0]
        df_P = pd.DataFrame(np.tile(case["bus"][:, 2], (T, 1)))
        df_Q = pd.DataFrame(np.zeros((T, nb)))
        df_min = pd.DataFrame(np.tile([-100.0], (T, 1)))
        df_max = pd.DataFrame(np.tile([100.0], (T, 1)))

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf_multistep(
                case,
                df_P,
                df_Q,
                T=T,
                formulation="lossy_dc",
                hvdc=[self._LINK],
                df_hvdc_min=df_min,
                df_hvdc_max=df_max,
            )
        assert "n_hvdc" in build.data

    def test_multistep_variables_are_lists(self):
        case = case9()
        T = 3
        nb = case["bus"].shape[0]
        df_P = pd.DataFrame(np.tile(case["bus"][:, 2], (T, 1)))
        df_Q = pd.DataFrame(np.zeros((T, nb)))

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf_multistep(
                case,
                df_P,
                df_Q,
                T=T,
                formulation="lossy_dc",
                hvdc=[self._LINK],
            )
        assert isinstance(build.variables["p_hvdc_in"], list)
        assert isinstance(build.variables["p_hvdc_out"], list)
        assert len(build.variables["p_hvdc_in"]) == T
        assert len(build.variables["p_hvdc_out"]) == T


# ---------------------------------------------------------------------------
# Gate 4 — live solve: lossy_dc with HVDC on case9
# ---------------------------------------------------------------------------


class TestHVDCLossyDCSolve:
    """Gate 4: live solve tests for lossy_dc formulation with HVDC.

    Uses case9, bus 4 → bus 9 link. Verifies optimality, flow conservation,
    and the loss relationship p_out = coeff * p_in.

    Convention B sign convention (from hvdc.py):
      p_in > 0 at from_bus: from_bus injects into grid (receives from DC link)
      p_out < 0 at to_bus: to_bus withdraws from grid (sends into DC link)
      For p_min_t >= 0 (from->to): p_out = -(1 - loss_frac) * p_in
    """

    _CASE = staticmethod(case9)

    def _Pd_mw(self, case):
        return case["bus"][:, 2]  # column PD, MW

    def test_lossless_unconstrained_solves_optimal(self):
        lnk = HVDCLink(from_bus=4, to_bus=9, p_min_mw=-1000.0, p_max_mw=1000.0)
        build = build_opf(self._CASE(), formulation="lossy_dc", hvdc=[lnk])
        build.solve()
        assert build.prob.status in ("optimal", "optimal_inaccurate")

    def test_lossless_unconstrained_flow_conservation(self):
        lnk = HVDCLink(from_bus=4, to_bus=9, p_min_mw=-1000.0, p_max_mw=1000.0)
        build = build_opf(self._CASE(), formulation="lossy_dc", hvdc=[lnk])
        build.solve()
        assert build.prob.status in ("optimal", "optimal_inaccurate")

        A = build.data["A"]
        Ch_from = build.data["Ch_from"]
        Ch_to = build.data["Ch_to"]
        Pd = build.data["Pd"]
        bMVA = build.data["baseMVA"]

        p_flows = build.variables["p_flows"].value
        Pg = build.variables["Pg"].value
        Cg = build.data["Cg"]
        p_in = build.variables["p_hvdc_in"].value
        p_out = build.variables["p_hvdc_out"].value

        balance = A @ p_flows + Cg @ Pg + (1.0 / bMVA) * (Ch_from @ p_in + Ch_to @ p_out)
        np.testing.assert_allclose(balance, Pd, atol=1e-4)

    def test_pinned_lossless_p_in_and_p_out(self):
        # Degenerate box pins p_in to 50 MW; lossless so p_out = -50 MW.
        lnk = HVDCLink(
            from_bus=4, to_bus=9, p_min_mw=50.0, p_max_mw=50.0, loss_percent=0.0
        )
        build = build_opf(self._CASE(), formulation="lossy_dc", hvdc=[lnk])
        build.solve()
        assert build.prob.status in ("optimal", "optimal_inaccurate")

        p_in = build.variables["p_hvdc_in"].value[0]
        p_out = build.variables["p_hvdc_out"].value[0]
        assert p_in == pytest.approx(50.0, abs=1e-3)
        assert p_out == pytest.approx(-50.0, abs=1e-3)

    def test_pinned_lossy_fixed_positive_direction(self):
        # p_min=p_max=60 MW, loss=5% -> p_out = -(1-0.05)*60 = -57
        loss_pct = 5.0
        lnk = HVDCLink(
            from_bus=4, to_bus=9, p_min_mw=60.0, p_max_mw=60.0, loss_percent=loss_pct
        )
        build = build_opf(self._CASE(), formulation="lossy_dc", hvdc=[lnk])
        build.solve()
        assert build.prob.status in ("optimal", "optimal_inaccurate")

        p_in = build.variables["p_hvdc_in"].value[0]
        p_out = build.variables["p_hvdc_out"].value[0]
        loss_frac = loss_pct / 100.0
        assert p_in == pytest.approx(60.0, abs=1e-3)
        assert p_out == pytest.approx(-(1.0 - loss_frac) * 60.0, abs=1e-3)

    def test_pinned_lossy_fixed_negative_direction_multistep(self):
        # Multistep T=1, per-step box pins p_in at -60 MW (to->from direction).
        # p_max_t=-60 <= 0 -> loss branch: p_out = -(1+loss_frac)*p_in = 63.
        case = self._CASE()
        nb = case["bus"].shape[0]
        loss_pct = 5.0
        lnk = HVDCLink(
            from_bus=4,
            to_bus=9,
            p_min_mw=-1000.0,
            p_max_mw=1000.0,
            loss_percent=loss_pct,
        )
        df_P = pd.DataFrame(np.tile(self._Pd_mw(case), (1, 1)))
        df_Q = pd.DataFrame(np.zeros((1, nb)))
        # Pin step 0 to -60 MW (degenerate box)
        df_min = pd.DataFrame([[-60.0]])
        df_max = pd.DataFrame([[-60.0]])

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf_multistep(
                case,
                df_P,
                df_Q,
                T=1,
                formulation="lossy_dc",
                hvdc=[lnk],
                df_hvdc_min=df_min,
                df_hvdc_max=df_max,
            )
        build.solve()
        assert build.prob.status in ("optimal", "optimal_inaccurate")

        p_in = build.variables["p_hvdc_in"][0].value[0]
        p_out = build.variables["p_hvdc_out"][0].value[0]
        loss_frac = loss_pct / 100.0
        assert p_in == pytest.approx(-60.0, abs=1e-3)
        assert p_out == pytest.approx(-(1.0 + loss_frac) * (-60.0), abs=1e-3)

    def test_zero_straddling_emits_warning(self):
        # Box straddles zero with loss > 0 -> UserWarning for lossless fallback.
        lnk = HVDCLink(
            from_bus=4, to_bus=9, p_min_mw=-100.0, p_max_mw=100.0, loss_percent=5.0
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            build_opf(self._CASE(), formulation="lossy_dc", hvdc=[lnk])
        hvdc_warns = [
            x
            for x in w
            if issubclass(x.category, UserWarning)
            and "straddles zero" in str(x.message)
        ]
        assert len(hvdc_warns) == 1

    def test_t1_multistep_matches_single_step_objective(self):
        # T=1 multistep must give the same optimal objective as single-step.
        case = self._CASE()
        nb = case["bus"].shape[0]
        lnk = HVDCLink(from_bus=4, to_bus=9, p_min_mw=-50.0, p_max_mw=50.0)
        df_P = pd.DataFrame(np.tile(self._Pd_mw(case), (1, 1)))
        df_Q = pd.DataFrame(np.zeros((1, nb)))
        df_min = pd.DataFrame([[-50.0]])
        df_max = pd.DataFrame([[50.0]])

        build_s = build_opf(case, formulation="lossy_dc", hvdc=[lnk])
        build_s.solve()

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build_m = build_opf_multistep(
                case,
                df_P,
                df_Q,
                T=1,
                formulation="lossy_dc",
                hvdc=[lnk],
                df_hvdc_min=df_min,
                df_hvdc_max=df_max,
            )
        build_m.solve()

        assert build_s.prob.status in ("optimal", "optimal_inaccurate")
        assert build_m.prob.status in ("optimal", "optimal_inaccurate")
        assert build_s.prob.value == pytest.approx(build_m.prob.value, rel=1e-4)


# ---------------------------------------------------------------------------
# Gate 5 — AC wiring: build.data / build.variables keys and shapes
# ---------------------------------------------------------------------------


class TestHVDCACWiring:
    """Gate 5 positive-wiring: ac build with hvdc= populates build.data
    and build.variables with HVDC keys. Detection contract: "n_hvdc" in
    build.data. Variables: p_hvdc_in / p_hvdc_out."""

    _LINK = HVDCLink(from_bus=4, to_bus=9, p_min_mw=-100.0, p_max_mw=100.0)

    def test_single_step_data_key_present(self):
        build = build_opf(case9(), formulation="ac", hvdc=[self._LINK])
        assert "n_hvdc" in build.data

    def test_single_step_n_hvdc_count(self):
        build = build_opf(case9(), formulation="ac", hvdc=[self._LINK])
        assert build.data["n_hvdc"] == 1

    def test_single_step_incidence_shapes(self):
        build = build_opf(case9(), formulation="ac", hvdc=[self._LINK])
        nb = build.data["nb"]
        assert build.data["Ch_from"].shape == (nb, 1)
        assert build.data["Ch_to"].shape == (nb, 1)

    def test_single_step_variables_present(self):
        build = build_opf(case9(), formulation="ac", hvdc=[self._LINK])
        assert "p_hvdc_in" in build.variables
        assert "p_hvdc_out" in build.variables

    def test_single_step_variable_shapes(self):
        build = build_opf(case9(), formulation="ac", hvdc=[self._LINK])
        assert build.variables["p_hvdc_in"].shape == (1,)
        assert build.variables["p_hvdc_out"].shape == (1,)

    def test_no_hvdc_key_absent_without_hvdc(self):
        build = build_opf(case9(), formulation="ac")
        assert "n_hvdc" not in build.data

    def test_multistep_data_key_present(self):
        case = case9()
        T = 2
        nb = case["bus"].shape[0]
        df_P = pd.DataFrame(np.tile(case["bus"][:, 2], (T, 1)))
        df_Q = pd.DataFrame(np.zeros((T, nb)))
        df_min = pd.DataFrame(np.tile([-100.0], (T, 1)))
        df_max = pd.DataFrame(np.tile([100.0], (T, 1)))

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf_multistep(
                case,
                df_P,
                df_Q,
                T=T,
                formulation="ac",
                hvdc=[self._LINK],
                df_hvdc_min=df_min,
                df_hvdc_max=df_max,
            )
        assert "n_hvdc" in build.data

    def test_multistep_variables_are_lists(self):
        case = case9()
        T = 2
        nb = case["bus"].shape[0]
        df_P = pd.DataFrame(np.tile(case["bus"][:, 2], (T, 1)))
        df_Q = pd.DataFrame(np.zeros((T, nb)))

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf_multistep(
                case,
                df_P,
                df_Q,
                T=T,
                formulation="ac",
                hvdc=[self._LINK],
            )
        assert isinstance(build.variables["p_hvdc_in"], list)
        assert isinstance(build.variables["p_hvdc_out"], list)
        assert len(build.variables["p_hvdc_in"]) == T
        assert len(build.variables["p_hvdc_out"]) == T


# ---------------------------------------------------------------------------
# Gate 5 — live solve: ac with HVDC on case9
# ---------------------------------------------------------------------------


class TestHVDCACSOlve:
    """Gate 5: live solve tests for ac formulation with HVDC.

    Uses case9, bus 4 -> bus 9 link. Verifies IPOPT convergence,
    p-balance includes HVDC injection, q-balance excludes HVDC
    (unity power factor), and lossless/lossy pinned flow relationships.

    Convention B sign convention:
      p_in > 0: from_bus injects into grid (receives from DC link)
      p_out < 0: to_bus withdraws from grid (sends into DC link)
      For p_min_t >= 0: p_out = -(1 - loss_frac) * p_in
    """

    _CASE = staticmethod(case9)

    def _Pd_mw(self, case):
        return case["bus"][:, 2]  # column PD, MW

    def test_lossless_unconstrained_solves_optimal(self):
        lnk = HVDCLink(from_bus=4, to_bus=9, p_min_mw=-1000.0, p_max_mw=1000.0)
        build = build_opf(self._CASE(), formulation="ac", hvdc=[lnk])
        build.solve()
        assert build.prob.status in ("optimal", "optimal_inaccurate")

    def test_p_balance_includes_hvdc_injection(self):
        # Pin the link so p_in is known; verify HVDC term appears in p-balance.
        lnk = HVDCLink(
            from_bus=4, to_bus=9, p_min_mw=50.0, p_max_mw=50.0, loss_percent=0.0
        )
        build = build_opf(self._CASE(), formulation="ac", hvdc=[lnk])
        build.solve()
        assert build.prob.status in ("optimal", "optimal_inaccurate")

        bMVA = build.data["baseMVA"]
        Cg = build.data["Cg"]
        Ch_from = build.data["Ch_from"]
        Ch_to = build.data["Ch_to"]
        Pd_pu = build.data["Pd"]

        p = build.variables["p"].value
        Pg = build.variables["Pg"].value
        p_in = build.variables["p_hvdc_in"].value
        p_out = build.variables["p_hvdc_out"].value

        hvdc_inj = (1.0 / bMVA) * (Ch_from @ p_in + Ch_to @ p_out)

        # p-balance holds WITH hvdc injection
        np.testing.assert_allclose(p, Cg @ Pg - Pd_pu + hvdc_inj, atol=1e-4)
        # HVDC injection is nonzero (link was actually wired in)
        assert np.abs(hvdc_inj).max() > 0.1 / bMVA

    def test_q_balance_excludes_hvdc_unity_pf(self):
        # Pin a large HVDC flow; verify q-balance has no HVDC addend.
        lnk = HVDCLink(
            from_bus=4, to_bus=9, p_min_mw=80.0, p_max_mw=80.0, loss_percent=0.0
        )
        build = build_opf(self._CASE(), formulation="ac", hvdc=[lnk])
        build.solve()
        assert build.prob.status in ("optimal", "optimal_inaccurate")

        Cg = build.data["Cg"]
        Qd_pu = build.data["Qd"]

        q = build.variables["q"].value
        Qg = build.variables["Qg"].value

        # q-balance holds WITHOUT any HVDC addend
        np.testing.assert_allclose(q, Cg @ Qg - Qd_pu, atol=1e-4)

    def test_pinned_lossless_p_in_and_p_out(self):
        # Degenerate box pins p_in to 50 MW; lossless -> p_out = -50 MW.
        lnk = HVDCLink(
            from_bus=4, to_bus=9, p_min_mw=50.0, p_max_mw=50.0, loss_percent=0.0
        )
        build = build_opf(self._CASE(), formulation="ac", hvdc=[lnk])
        build.solve()
        assert build.prob.status in ("optimal", "optimal_inaccurate")

        p_in = build.variables["p_hvdc_in"].value[0]
        p_out = build.variables["p_hvdc_out"].value[0]
        assert p_in == pytest.approx(50.0, abs=1e-3)
        assert p_out == pytest.approx(-50.0, abs=1e-3)

    def test_pinned_lossy_fixed_positive_direction(self):
        # p_min=p_max=60 MW, loss=5% -> p_out = -(1-0.05)*60 = -57
        loss_pct = 5.0
        lnk = HVDCLink(
            from_bus=4, to_bus=9, p_min_mw=60.0, p_max_mw=60.0, loss_percent=loss_pct
        )
        build = build_opf(self._CASE(), formulation="ac", hvdc=[lnk])
        build.solve()
        assert build.prob.status in ("optimal", "optimal_inaccurate")

        p_in = build.variables["p_hvdc_in"].value[0]
        p_out = build.variables["p_hvdc_out"].value[0]
        loss_frac = loss_pct / 100.0
        assert p_in == pytest.approx(60.0, abs=1e-3)
        assert p_out == pytest.approx(-(1.0 - loss_frac) * 60.0, abs=1e-3)

    def test_zero_straddling_emits_warning(self):
        # Box straddles zero with loss > 0 -> UserWarning for lossless fallback.
        lnk = HVDCLink(
            from_bus=4, to_bus=9, p_min_mw=-100.0, p_max_mw=100.0, loss_percent=5.0
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            build_opf(self._CASE(), formulation="ac", hvdc=[lnk])
        hvdc_warns = [
            x
            for x in w
            if issubclass(x.category, UserWarning)
            and "straddles zero" in str(x.message)
        ]
        assert len(hvdc_warns) == 1

    def test_t1_multistep_matches_single_step_objective(self):
        # T=1 multistep must give the same optimal objective as single-step.
        case = self._CASE()
        nb = case["bus"].shape[0]
        lnk = HVDCLink(from_bus=4, to_bus=9, p_min_mw=-50.0, p_max_mw=50.0)
        df_P = pd.DataFrame(np.tile(self._Pd_mw(case), (1, 1)))
        df_Q = pd.DataFrame(np.zeros((1, nb)))
        df_min = pd.DataFrame([[-50.0]])
        df_max = pd.DataFrame([[50.0]])

        build_s = build_opf(case, formulation="ac", hvdc=[lnk])
        build_s.solve()

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build_m = build_opf_multistep(
                case,
                df_P,
                df_Q,
                T=1,
                formulation="ac",
                hvdc=[lnk],
                df_hvdc_min=df_min,
                df_hvdc_max=df_max,
            )
        build_m.solve()

        assert build_s.prob.status in ("optimal", "optimal_inaccurate")
        assert build_m.prob.status in ("optimal", "optimal_inaccurate")
        assert build_s.prob.value == pytest.approx(build_m.prob.value, rel=1e-3)


# ---------------------------------------------------------------------------
# Gate 6 - result extraction through extract_results()
# ---------------------------------------------------------------------------


class TestHVDCResultExtraction:
    """Gate 6: extract_results() surfaces HVDC keys with correct shapes and
    the derived hvdc_loss, for both ac and lossy_dc, single-step."""

    def _pinned_link(self, loss_pct=0.0):
        # degenerate box pins p_in = 60 MW (positive/from->to direction)
        return HVDCLink(
            from_bus=4, to_bus=9, p_min_mw=60.0, p_max_mw=60.0, loss_percent=loss_pct
        )

    def test_ac_keys_present_and_shapes(self):
        build = build_opf(case9(), formulation="ac", hvdc=[self._pinned_link()])
        build.solve()
        r = extract_results(build)
        assert "p_hvdc_in" in r and "p_hvdc_out" in r and "hvdc_loss" in r
        assert r["p_hvdc_in"].shape == (1,)
        assert r["p_hvdc_out"].shape == (1,)
        assert r["hvdc_loss"].shape == (1,)

    def test_dc_keys_present_and_shapes(self):
        build = build_opf(case9(), formulation="lossy_dc", hvdc=[self._pinned_link()])
        build.solve()
        r = extract_results(build)
        assert "p_hvdc_in" in r and "p_hvdc_out" in r and "hvdc_loss" in r
        assert r["p_hvdc_in"].shape == (1,)

    def test_hvdc_loss_nonneg(self):
        build = build_opf(
            case9(), formulation="ac", hvdc=[self._pinned_link(loss_pct=5.0)]
        )
        build.solve()
        r = extract_results(build)
        assert np.all(r["hvdc_loss"] >= -1e-6)

    def test_hvdc_loss_equals_in_plus_out(self):
        build = build_opf(
            case9(), formulation="ac", hvdc=[self._pinned_link(loss_pct=5.0)]
        )
        build.solve()
        r = extract_results(build)
        np.testing.assert_allclose(
            r["hvdc_loss"], r["p_hvdc_in"] + r["p_hvdc_out"], atol=1e-6
        )

    def test_loss_law_through_extracted_values(self):
        # p_in pinned 60, loss 5% -> p_out = -(1-0.05)*60 = -57, loss = 3
        build = build_opf(
            case9(), formulation="ac", hvdc=[self._pinned_link(loss_pct=5.0)]
        )
        build.solve()
        r = extract_results(build)
        assert r["p_hvdc_in"][0] == pytest.approx(60.0, abs=1e-3)
        assert r["p_hvdc_out"][0] == pytest.approx(-57.0, abs=1e-3)
        assert r["hvdc_loss"][0] == pytest.approx(3.0, abs=1e-3)

    def test_keys_absent_without_hvdc(self):
        build = build_opf(case9(), formulation="ac")
        build.solve()
        r = extract_results(build)
        assert "p_hvdc_in" not in r
        assert "hvdc_loss" not in r


# ---------------------------------------------------------------------------
# Gate 6b - case9_dcline internal consistency (NOT a Pypower value-match)
# ---------------------------------------------------------------------------


class TestHVDCCase9DclineConsistency:
    """Gate 6b: solve the real case9_dcline case with HVDC imported from its
    dcline table and assert INTERNAL CONSISTENCY, not a value-match against
    the Pypower fixture.

    Why not a value-match: cvxopf's HVDC MVP models a DC line as a unity-PF
    real-power injection, whereas Pypower models it as two dummy generators at
    PV buses with reactive bounds (voltage-regulating reactive sources). That
    device-model difference reshapes the AC solution. AC-OPF is also nonconvex
    (sin/cos power flow), so the two solvers may land on different local
    optima. Either way objective/dispatch do not match (cvxopf ~5490 vs
    fixture ~6446); see memories/case9-dcline-branch-limit-gap.md. Branch
    limits and PWL cost were ruled out as causes.

    Links import via hvdc_from_dcline(dcline) with NO cost table (Option A:
    matches the fixture script's del dclinecost; links are zero-cost).
    """

    def _build(self):
        case = case9_dcline()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            links = hvdc_from_dcline(case["dcline"])
            build = build_opf(case, formulation="ac", hvdc=links)
        build.solve()
        return build, links

    def test_solves_optimal(self):
        build, _ = self._build()
        assert build.prob.status in ("optimal", "optimal_inaccurate")

    def test_loss0_warning_fires_on_import(self):
        # row 0 (30->4) has loss0=1 -> hvdc_from_dcline warns
        case = case9_dcline()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            hvdc_from_dcline(case["dcline"])
        loss0_warns = [
            x
            for x in w
            if issubclass(x.category, UserWarning) and "loss0" in str(x.message).lower()
        ]
        assert len(loss0_warns) >= 1

    def test_ac_nodal_balance_includes_hvdc(self):
        build, _ = self._build()
        assert build.prob.status in ("optimal", "optimal_inaccurate")
        d = build.data
        bMVA = d["baseMVA"]
        p = build.variables["p"].value
        Cg = d["Cg"]
        Pg = build.variables["Pg"].value
        Pd = d["Pd"]
        Ch_from = d["Ch_from"]
        Ch_to = d["Ch_to"]
        p_in = build.variables["p_hvdc_in"].value
        p_out = build.variables["p_hvdc_out"].value
        hvdc_inj = (1.0 / bMVA) * (Ch_from @ p_in + Ch_to @ p_out)
        np.testing.assert_allclose(p, Cg @ Pg - Pd + hvdc_inj, atol=1e-4)

    def test_loss_law_fixed_direction_links(self):
        # all three imported links are fixed-direction (Pmin>=0): from->to.
        build, links = self._build()
        r = extract_results(build)
        for k, lk in enumerate(links):
            frac = lk.loss_percent / 100.0
            assert r["p_hvdc_out"][k] == pytest.approx(
                -(1.0 - frac) * r["p_hvdc_in"][k], abs=1e-3
            )

    def test_hvdc_loss_nonneg(self):
        build, _ = self._build()
        r = extract_results(build)
        assert np.all(r["hvdc_loss"] >= -1e-6)


# ===========================================================================
# Gate 7 (TestHVDCYbusAgreement): cvxopf's Ybus for case9_dcline must equal
# Pypower's makeYbus output. This pins the load-bearing HVDC assumption that
# DC lines contribute NOTHING to Ybus (they are modelled as nodal injections,
# not admittance branches). Compared against a committed static fixture
# (generated in the isolated numpy-2.2.6 sandbox by
# scripts/generate_pypower_fixtures.py) rather than a live makeYbus call, per
# the no-pypower-dependency rule.
# ===========================================================================

_FIXTURES = Path(__file__).parent / "fixtures"


class TestHVDCYbusAgreement:
    def _load_ybus_fixture(self):
        path = _FIXTURES / "case9_dcline_ybus_pypower_reference.json"
        if not path.exists() or path.stat().st_size == 0:
            pytest.skip(
                f"Fixture {path.name} missing/empty. "
                "Run: uv run scripts/generate_pypower_fixtures.py"
            )
        with open(path) as f:
            return json.load(f)

    def test_ybus_matches_pypower(self):
        """cvxopf's build.data["Ybus"] equals Pypower's makeYbus, aligned by
        external bus ID -- proving the dcline table does not enter Ybus."""
        fix = self._load_ybus_fixture()
        fix_Y = np.asarray(fix["Ybus_real"]) + 1j * np.asarray(fix["Ybus_imag"])
        fix_bus_ids = list(fix["bus_ids"])  # external ids in fixture row order

        build = build_opf(case9_dcline(), formulation="ac")
        cvx_Y = build.data["Ybus"]
        ext_to_int = build.data["ext_to_int"]
        assert ext_to_int is not None, "case9_dcline must be reindexed"

        # Permute the fixture Ybus into cvxopf's internal order: fixture row i
        # is external bus fix_bus_ids[i] -> cvxopf internal ext_to_int[...].
        nb = cvx_Y.shape[0]
        perm = np.empty(nb, dtype=int)
        for i, ext_id in enumerate(fix_bus_ids):
            perm[ext_to_int[ext_id]] = i
        fix_Y_internal = fix_Y[np.ix_(perm, perm)]

        assert cvx_Y.shape == fix_Y_internal.shape
        max_abs_diff = np.abs(cvx_Y - fix_Y_internal).max()
        assert max_abs_diff < 1e-9, (
            f"cvxopf Ybus disagrees with Pypower (max abs diff {max_abs_diff:.2e}); "
            "DC lines must contribute nothing to Ybus."
        )
