"""Compensated-arithmetic evidence handling without OPF solves."""

from decimal import Decimal

import pytest

from experiments.socp_conditioning import compensated_cone as c


def test_exact_determinant_preserves_small_negative_term():
    assert c.precise_determinant([1.0, 1.0, 1e-9]) < 0
    assert c.precise_determinant([5.0, 3.0, 4.0]) == 0


def test_incomplete_native_fixture_gate_rejected():
    with pytest.raises(ValueError, match="missing"):
        c.validate_fixtures("")


def test_prefix_comparison_ignores_only_time():
    old = [dict(iterations=0, gap=1.0, solve_time=2.0)]
    assert c.prefix_matches([old[0] | dict(solve_time=3.0)], old, 0)
    assert not c.prefix_matches([old[0] | dict(gap=0.9)], old, 0)


def test_fallback_evidence_records_sign_without_clipping():
    events = c.fallbacks("""TRACE ITER iter=32 mu=1e-14
TRACE COMPENSATED old=0.0 scaled_det=-1e-18 scale=1.0 recovered=0.0 z=[1.0, 1.0, 1e-9]
""")
    assert events[0]["iteration"] == 32
    assert Decimal(events[0]["exact_determinant"]) < 0
    assert events[0]["recovered"] == 0
