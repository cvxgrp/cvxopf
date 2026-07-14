"""
Test suite for Milestone 7 HVDC transmission link model.

Gate 1 (TestHVDCUnit): pure logic — validation, incidence, static box,
  hvdc_from_dcline, hvdc_cost_expr DCP check, ac_ delegates to dc_.
Gate 2 (TestHVDCCVXPY): CVXPY component methods — box shapes, loss branches,
  mixed batches, Convention B sign check. No live solve in either gate.
Gate 3 (TestHVDCWiring, silent-ignore half): singlenode builds with hvdc=
  accepted and dropped silently; "n_hvdc" never appears in build.data.
  Positive-wiring half (ac & lossy_dc) deferred to Gate 4 (T4).
"""

import warnings

import numpy as np
import pandas as pd
import pytest
import cvxpy as cp

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
from cvxopf.problem import build_opf, build_opf_multistep
from cvxopf.testcases import make_singlenode_case


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _link(from_bus=1, to_bus=2, p_max_mw=100.0, p_min_mw=None,
          p_scheduled_mw=50.0, bandwidth_mw=10.0, mode="band",
          loss_percent=0.0, cost_coeffs=(0.0, 0.0, 0.0)):
    return HVDCLink(
        from_bus=from_bus, to_bus=to_bus, p_max_mw=p_max_mw,
        p_min_mw=p_min_mw, p_scheduled_mw=p_scheduled_mw,
        bandwidth_mw=bandwidth_mw, mode=mode,
        loss_percent=loss_percent, cost_coeffs=cost_coeffs,
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
        # degenerate box is allowed at construction; coincident bounds handle it
        _validate_hvdc([_link(p_min_mw=50.0, p_max_mw=50.0)], _EXT_BUS_IDS)

    def test_bandwidth_negative_raises(self):
        with pytest.raises(ValueError, match="bandwidth_mw must be >= 0"):
            _validate_hvdc([_link(bandwidth_mw=-1.0)], _EXT_BUS_IDS)

    def test_loss_negative_raises(self):
        with pytest.raises(ValueError, match="loss_percent must be >= 0"):
            _validate_hvdc([_link(loss_percent=-1.0)], _EXT_BUS_IDS)

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="mode must be one of"):
            _validate_hvdc([_link(mode="turbo")], _EXT_BUS_IDS)

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
        assert Ch_to.shape   == (_NB, 0)

    def test_single_link_shapes(self):
        lnk = _link(from_bus=1, to_bus=3)
        Ch_from, Ch_to = _make_hvdc_incidence_matrices([lnk], _NB, _EXT_TO_INT)
        assert Ch_from.shape == (_NB, 1)
        assert Ch_to.shape   == (_NB, 1)

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
        assert Ch_to[1, 0]   == 1.0  # link 0 to_bus=2 -> int 1
        assert Ch_to[2, 1]   == 1.0  # link 1 to_bus=3 -> int 2


class TestHVDCStaticBox:

    def test_scheduled_degenerate(self):
        lnk = _link(mode="scheduled", p_scheduled_mw=30.0, p_max_mw=100.0)
        p_min, p_max = _hvdc_static_box([lnk])
        assert p_min[0] == pytest.approx(30.0)
        assert p_max[0] == pytest.approx(30.0)

    def test_scheduled_negative_sched(self):
        lnk = _link(mode="scheduled", p_scheduled_mw=-20.0, p_max_mw=100.0)
        p_min, p_max = _hvdc_static_box([lnk])
        assert p_min[0] == pytest.approx(-20.0)
        assert p_max[0] == pytest.approx(-20.0)

    def test_downward_positive_sched(self):
        lnk = _link(mode="downward", p_scheduled_mw=50.0, p_max_mw=100.0)
        p_min, p_max = _hvdc_static_box([lnk])
        assert p_min[0] == pytest.approx(0.0)
        assert p_max[0] == pytest.approx(50.0)

    def test_downward_negative_sched(self):
        lnk = _link(mode="downward", p_scheduled_mw=-50.0, p_max_mw=100.0)
        p_min, p_max = _hvdc_static_box([lnk])
        assert p_min[0] == pytest.approx(-50.0)
        assert p_max[0] == pytest.approx(0.0)

    def test_band_intersected(self):
        # sched=40, bw=20 -> [20, 60] intersected with [-100, 100] -> [20, 60]
        lnk = _link(mode="band", p_scheduled_mw=40.0, bandwidth_mw=20.0,
                    p_max_mw=100.0)
        p_min, p_max = _hvdc_static_box([lnk])
        assert p_min[0] == pytest.approx(20.0)
        assert p_max[0] == pytest.approx(60.0)

    def test_band_clamped_by_p_max(self):
        # sched=90, bw=20 -> [70, 110] intersected with [-100, 100] -> [70, 100]
        lnk = _link(mode="band", p_scheduled_mw=90.0, bandwidth_mw=20.0,
                    p_max_mw=100.0)
        p_min, p_max = _hvdc_static_box([lnk])
        assert p_min[0] == pytest.approx(70.0)
        assert p_max[0] == pytest.approx(100.0)

    def test_band_degenerate_zero_bandwidth(self):
        lnk = _link(mode="band", p_scheduled_mw=40.0, bandwidth_mw=0.0,
                    p_max_mw=100.0)
        p_min, p_max = _hvdc_static_box([lnk])
        assert p_min[0] == pytest.approx(40.0)
        assert p_max[0] == pytest.approx(40.0)

    def test_free_default_symmetric(self):
        lnk = _link(mode="free", p_max_mw=100.0)
        p_min, p_max = _hvdc_static_box([lnk])
        assert p_min[0] == pytest.approx(-100.0)
        assert p_max[0] == pytest.approx(100.0)

    def test_free_explicit_p_min(self):
        lnk = _link(mode="free", p_max_mw=100.0, p_min_mw=0.0)
        p_min, p_max = _hvdc_static_box([lnk])
        assert p_min[0] == pytest.approx(0.0)
        assert p_max[0] == pytest.approx(100.0)

    def test_p_min_none_defaults_band(self):
        # p_min_mw=None in band mode -> default -p_max_mw
        lnk = _link(mode="band", p_max_mw=50.0, p_min_mw=None,
                    p_scheduled_mw=0.0, bandwidth_mw=10.0)
        p_min, p_max = _hvdc_static_box([lnk])
        # [-10, 10] intersected with [-50, 50] -> [-10, 10]
        assert p_min[0] == pytest.approx(-10.0)
        assert p_max[0] == pytest.approx(10.0)

    def test_p_min_none_defaults_free(self):
        lnk = _link(mode="free", p_max_mw=80.0, p_min_mw=None)
        p_min, p_max = _hvdc_static_box([lnk])
        assert p_min[0] == pytest.approx(-80.0)

    def test_p_min_none_defaults_downward(self):
        # downward with p_min_mw=None; p_sched>=0 -> [0, p_sched]
        lnk = _link(mode="downward", p_max_mw=100.0, p_min_mw=None,
                    p_scheduled_mw=60.0)
        p_min, p_max = _hvdc_static_box([lnk])
        assert p_min[0] == pytest.approx(0.0)
        assert p_max[0] == pytest.approx(60.0)


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

    def test_all_links_mode_free(self):
        dcline, dclinecost = self._case_tables()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            links = hvdc_from_dcline(dcline, dclinecost)
        for lnk in links:
            assert lnk.mode == "free"

    def test_loss0_warning_emitted(self):
        # row 0 of t_case9_dcline has loss0=1 (nonzero)
        dcline, dclinecost = self._case_tables()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            hvdc_from_dcline(dcline, dclinecost)
        msgs = [str(x.message) for x in w if issubclass(x.category, UserWarning)]
        assert any("loss0" in m.lower() or "fixed converter" in m.lower()
                   for m in msgs)

    def test_no_warning_when_loss0_zero(self):
        # Build a minimal table with no loss0
        dcline = np.array([[1, 2, 1, 10.0, 9.0, 0, 0, 1, 1, 0, 20, 0, 0, 0, 0,
                            0.0, 0.0]])
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            hvdc_from_dcline(dcline)
        assert not any(issubclass(x.category, UserWarning) for x in w)

    def test_pmin_pmax_mapped(self):
        dcline, dclinecost = self._case_tables()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            links = hvdc_from_dcline(dcline, dclinecost)
        # All links should have finite p_min_mw and p_max_mw
        for lnk in links:
            assert lnk.p_min_mw is not None
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
        dcline = np.array([[1, 2, 1, 10.0, 9.0, 0, 0, 1, 1, 0, 20, 0, 0, 0, 0,
                            0.0, 0.01]])
        cost_row = np.array([[2, 0, 0, 3, 5.0, 3.0, 1.0]])
        links = hvdc_from_dcline(dcline, cost_row)
        assert len(links) == 1
        assert links[0].cost_coeffs == pytest.approx((1.0, 3.0, 5.0))

    def test_linear_dclinecost_n2_padding(self):
        # Linear row: [2, 0, 0, 2, 7.3, 0.0] -> (c0=0.0, c1=7.3, c2=0.0)
        dcline = np.array([[1, 2, 1, 10.0, 9.0, 0, 0, 1, 1, 0, 20, 0, 0, 0, 0,
                            0.0, 0.05]])
        cost_row = np.array([[2, 0, 0, 2, 7.3, 0.0]])
        links = hvdc_from_dcline(dcline, cost_row)
        assert links[0].cost_coeffs == pytest.approx((0.0, 7.3, 0.0))

    def test_degree_gt3_raises(self):
        dcline = np.array([[1, 2, 1, 10.0, 9.0, 0, 0, 1, 1, 0, 20, 0, 0, 0, 0,
                            0.0, 0.0]])
        cost_row = np.array([[2, 0, 0, 4, 1.0, 2.0, 3.0, 4.0]])
        with pytest.raises(ValueError, match="degree 4"):
            hvdc_from_dcline(dcline, cost_row)

    def test_no_dclinecost_defaults_zero(self):
        dcline = np.array([[1, 2, 1, 10.0, 9.0, 0, 0, 1, 1, 0, 20, 0, 0, 0, 0,
                            0.0, 0.0]])
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
        links = [_link(from_bus=1, to_bus=2, p_max_mw=100.0,
                       p_min_mw=0.0, mode="free")]
        p_in  = cp.Variable((1,))
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

    def test_returns_four_tuple(self):
        links = [_link(from_bus=1, to_bus=2, mode="free",
                       p_max_mw=100.0, p_min_mw=-100.0)]
        inj, p_in, p_out, inv_bMVA = hvdc_injections(links, _EXT_TO_INT)
        assert isinstance(p_in,  cp.Variable)
        assert isinstance(p_out, cp.Variable)
        assert isinstance(inv_bMVA, cp.Parameter)
        assert p_in.shape  == (1,)
        assert p_out.shape == (1,)

    def test_injection_is_cvxpy_expression(self):
        links = [_link(from_bus=1, to_bus=2, mode="free",
                       p_max_mw=100.0, p_min_mw=-100.0)]
        inj, _, _, _ = hvdc_injections(links, _EXT_TO_INT)
        assert isinstance(inj, cp.atoms.atom.Atom) or hasattr(inj, 'is_affine')

    def test_parameter_unset(self):
        links = [_link(from_bus=1, to_bus=2, mode="free",
                       p_max_mw=100.0, p_min_mw=-100.0)]
        _, _, _, inv_bMVA = hvdc_injections(links, _EXT_TO_INT)
        assert inv_bMVA.value is None

    def test_convention_b_sign_lossless(self):
        # For a lossless link with p_in > 0:
        #   from_bus gets +p_in/baseMVA (injects into grid)
        #   to_bus gets -p_in/baseMVA (withdraws from grid)
        baseMVA = 100.0
        links = [_link(from_bus=1, to_bus=2, mode="free",
                       p_max_mw=100.0, p_min_mw=-100.0, loss_percent=0.0)]
        inj, p_in, p_out, inv_bMVA = hvdc_injections(links, _EXT_TO_INT)
        inv_bMVA.value = 1.0 / baseMVA

        p_min_t = np.array([-100.0])
        p_max_t = np.array([100.0])
        constrs = dc_operating_constraints(links, p_in, p_out, p_min_t, p_max_t)

        # Set p_in = 50 MW
        prob = cp.Problem(
            cp.Minimize(0),
            constrs + [p_in == 50.0],
        )
        prob.solve(solver=cp.CLARABEL)
        assert prob.status in ("optimal", "optimal_inaccurate")

        inj_val = inj.value  # shape (nb=3,)
        # from_bus=1 -> internal 0: should be +50/100 = +0.5
        assert inj_val[0] == pytest.approx(+0.5, abs=1e-5)
        # to_bus=2 -> internal 1: should be -50/100 = -0.5
        assert inj_val[1] == pytest.approx(-0.5, abs=1e-5)
        # bus 3 uninvolved: 0
        assert inj_val[2] == pytest.approx(0.0,  abs=1e-5)


class TestHVDCOperatingConstraints:

    def test_degenerate_box_no_extra_equality(self):
        # Degenerate box: p_min_t == p_max_t. Pin via coincident bounds only.
        links = [_link(from_bus=1, to_bus=2, mode="scheduled",
                       p_scheduled_mw=40.0, p_max_mw=100.0)]
        p_in  = cp.Variable((1,))
        p_out = cp.Variable((1,))
        p_min_t = np.array([40.0])
        p_max_t = np.array([40.0])
        constrs = dc_operating_constraints(links, p_in, p_out, p_min_t, p_max_t)

        # Must return exactly 3 items (lower bound, upper bound, loss equality)
        # — NO extra equality for "p_in == p_sched"
        assert len(constrs) == 3

        # p_in and p_out are still cp.Variable (not numpy scalars)
        assert isinstance(p_in,  cp.Variable)
        assert isinstance(p_out, cp.Variable)

    def test_fixed_direction_positive_lossy_branch(self):
        # p_min_t >= 0 -> from->to branch: coeff = -(1 - loss_frac)
        loss_pct = 5.0
        links = [_link(from_bus=1, to_bus=2, loss_percent=loss_pct,
                       mode="free", p_max_mw=100.0, p_min_mw=0.0)]
        p_in  = cp.Variable((1,))
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
        loss_pct = 5.0
        links = [_link(from_bus=1, to_bus=2, loss_percent=loss_pct,
                       mode="free", p_max_mw=100.0, p_min_mw=-100.0)]
        p_in  = cp.Variable((1,))
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
        links = [_link(from_bus=1, to_bus=2, loss_percent=2.0,
                       mode="free", p_max_mw=100.0, p_min_mw=-100.0)]
        p_in  = cp.Variable((1,))
        p_out = cp.Variable((1,))
        p_min_t = np.array([-100.0])
        p_max_t = np.array([100.0])

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            constrs = dc_operating_constraints(links, p_in, p_out,
                                               p_min_t, p_max_t)
        assert any(issubclass(x.category, UserWarning) for x in w)

        # Lossless: p_out == -p_in
        prob = cp.Problem(cp.Minimize(0), constrs + [p_in == 60.0])
        prob.solve(solver=cp.CLARABEL)
        assert p_out.value[0] == pytest.approx(-60.0, abs=1e-5)

    def test_zero_straddling_lossless_no_warning(self):
        # Box straddles zero but loss=0 -> lossless, no UserWarning
        links = [_link(from_bus=1, to_bus=2, loss_percent=0.0,
                       mode="free", p_max_mw=100.0, p_min_mw=-100.0)]
        p_in  = cp.Variable((1,))
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
            _link(from_bus=1, to_bus=2, loss_percent=4.0,
                  mode="free", p_max_mw=100.0, p_min_mw=0.0),    # fixed
            _link(from_bus=2, to_bus=3, loss_percent=4.0,
                  mode="free", p_max_mw=100.0, p_min_mw=-100.0), # straddling
        ]
        p_in  = cp.Variable((2,))
        p_out = cp.Variable((2,))
        p_min_t = np.array([0.0, -100.0])
        p_max_t = np.array([100.0, 100.0])

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            constrs = dc_operating_constraints(links, p_in, p_out,
                                               p_min_t, p_max_t)

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
        {"P_max_MW": 200.0, "cost_coeffs": (0.0, 1.0, 0.01)},
        {"P_max_MW": 200.0, "cost_coeffs": (0.0, 2.0, 0.02)},
    ]
    _LINK = HVDCLink(from_bus=1, to_bus=2, p_max_mw=50.0)

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
                case, df_P, df_Q, T=T,
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
                case, df_P, df_Q, T=T,
                formulation="singlenode_dc",
                hvdc=[self._LINK],
            )
        assert "n_hvdc" not in build.data
        tile_warns = [x for x in w if issubclass(x.category, UserWarning)
                      and "hvdc" in str(x.message).lower()]
        assert len(tile_warns) == 1
