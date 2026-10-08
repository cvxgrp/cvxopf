"""Exact binary64 cone-boundary diagnostics; no optimization calls."""

from decimal import Decimal

import numpy as np
import pytest

from experiments.socp_conditioning import cone_interiority as c
from experiments.socp_conditioning import cone_interiority_analysis as a


def test_margin_distinguishes_interior_boundary_and_exterior():
    assert c.soc_metrics([np.nextafter(1.0, 2.0), 1.0])["strictly_interior"]
    assert Decimal(c.soc_metrics([1.0, 1.0])["margin"]) == 0
    assert Decimal(c.soc_metrics([1.0, 1.0, 1e-9])["margin"]) < 0
    assert not c.soc_metrics([-2.0, 1.0])["strictly_interior"]


@pytest.mark.parametrize("values", [[1], [1, float("nan")], [[1, 2]]])
def test_invalid_cone_rejected(values):
    with pytest.raises(ValueError):
        c.soc_metrics(values)


def test_failure_parser_retains_actual_preceding_step():
    log = """TRACE CONE_ADD alpha=0.8
TRACE CONE_RESCALE inverse=0.5
TRACE ITER iter=32 mu=1e-14
TRACE CONE_CHECK reason=input sscale=0.0 zscale=1.0
TRACE CONE_FAILURE rows=12..15 kind=SecondOrderCone mu=1e-14 s=[1.0, 1.0, 0.0] z=[2.0, 1.0, 0.0]
TRACE CONE_PREVIOUS s=[2.0, 1.0, 0.0] z=[3.0, 1.0, 0.0] ds=[-1.0, 0.0, 0.0] dz=[0.0, 0.0, 0.0]
"""
    result = c.parse_failure(log)
    assert result["rows"] == [12, 15]
    assert result["iteration"] == 32
    assert result["preceding_applied_alpha"] == 0.8
    assert not result["s_metrics"]["strictly_interior"]
    assert result["previous"]["s_metrics"]["strictly_interior"]
    with pytest.raises(ValueError):
        c.parse_failure(log + log)


def test_replay_must_match_vectors_and_termination(monkeypatch):
    monkeypatch.setattr(c.lu.k, "validate_arm", lambda *a: None)
    raw = dict(x=[1.0], s=[2.0], z=[3.0])
    native = dict(
        status="NumericalError",
        iterations=32,
        gap_abs=1e-9,
        gap_rel=1e-10,
        res_primal=1e-12,
        res_dual=1e-12,
    )
    r = dict(raw_clarabel=raw, native=native)
    c.verify_replay(r, {}, r)
    with pytest.raises(ValueError, match="vectors"):
        c.verify_replay(r | dict(raw_clarabel=raw | dict(x=[2.0])), {}, r)
    with pytest.raises(ValueError, match="termination"):
        c.verify_replay(r | dict(native=native | dict(iterations=33)), {}, r)


def test_actual_failed_vector_is_interior_despite_zero_native_margin():
    # Exact binary64 vector captured at native iteration 32, before unscaling.
    z = [
        2.334893449993115,
        -2.33471439015205,
        -0.02762315770132989,
        -0.008549876975618733,
    ]
    assert a.stable_norm(z[1:]) == z[0]
    metrics = c.soc_metrics(z)
    assert metrics["strictly_interior"]
    assert float(metrics["margin"]) == pytest.approx(
        2.375732958286822e-17, rel=1e-14, abs=0
    )
    assert 0 < float(metrics["margin"]) / metrics["leading_ulp"] < 0.1


def test_step_reconstruction_rejects_mismatched_state_or_rescaling():
    f = dict(
        previous=dict(s=[2.0, 1.0], ds=[-0.5, 0.0]),
        preceding_applied_alpha=0.5,
        following_rescale_inverse=None,
        s=[1.75, 1.0],
    )
    assert a.step_evidence(f, "s")["rounded_update_matches"]
    with pytest.raises(ValueError, match="preceding step"):
        a.step_evidence(f | dict(s=[1.0, 1.0]), "s")
    with pytest.raises(ValueError, match="rescaling"):
        a.step_evidence(f | dict(following_rescale_inverse=2.0), "s")
