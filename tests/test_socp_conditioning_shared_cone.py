"""Shared cone evidence checks; no OPF optimizer calls."""

import pytest
from decimal import Decimal

from experiments.socp_conditioning import shared_cone as s
from experiments.socp_conditioning import cone_interiority_analysis as a


def test_missing_native_fixtures_fail():
    with pytest.raises(ValueError, match="incomplete"):
        s.validate_fixtures("")


def test_first_difference_does_not_confuse_timing_with_numerics():
    old = [
        dict(iterations=0, gap=1.0, solve_time=1.0),
        dict(iterations=1, gap=0.1, solve_time=2.0),
    ]
    same = [v | dict(solve_time=10.0) for v in old]
    assert s.first_difference(same, old) is None
    same[1]["gap"] = 0.2
    assert s.first_difference(same, old) == dict(iteration=1, fields=["gap"])


def test_captured_update_rounding_crosses_boundary_not_exact_step():
    x = [
        1.3229452277627547,
        -1.322865842725549,
        -0.011389211184360338,
        0.008962347843471937,
    ]
    direction = [
        -3.8001326541555794e-11,
        3.8267270534576097e-11,
        -4.3610362072530076e-12,
        3.3488583071055514e-11,
    ]
    alpha = 0.19386048016222465
    rounded = [alpha * dv + xv for xv, dv in zip(x, direction, strict=True)]
    failure = dict(
        previous=dict(z=x, dz=direction),
        z=rounded,
        preceding_applied_alpha=alpha,
        following_rescale_inverse=None,
    )
    evidence = a.step_evidence(failure, "z")
    assert Decimal(evidence["exact_arithmetic_update_margin"]) > 0
    assert alpha < evidence["boundary"]["first_nonnegative_boundary"]
    assert Decimal(s.c.c.soc_metrics(rounded)["margin"]) < 0
    failure["z"] = x
    with pytest.raises(ValueError, match="does not reproduce"):
        a.step_evidence(failure, "z")
