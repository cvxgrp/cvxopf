"""Independent arithmetic, algebra and retained-control checks; no solves."""

from decimal import Decimal
from fractions import Fraction
from itertools import combinations, product
import math

import cvxpy as cp
import numpy as np
import pytest

from experiments.case118_tracy_2021 import e3_prepared
from experiments.tracy_battery_transfer import model as transfer
from experiments.tracy_battery_transfer_slice import model
from experiments.tracy_battery_transfer_loop.analyze import (
    I,
    PI,
    atan,
    forbid_solves,
    prove_loop,
    slack_metrics,
)


def test_intervals_against_exact_rationals():
    for a, b in product([I("-1.2", "-.7"), I(".94", "1.06"), I("2", "3")], repeat=2):
        for out, op in [
            (a + b, lambda x, y: x + y),
            (a - b, lambda x, y: x - y),
            (a * b, lambda x, y: x * y),
            (a / b, lambda x, y: x / y),
        ]:
            values = [
                op(Fraction(x), Fraction(y))
                for x, y in product((a.lo, a.hi), (b.lo, b.hi))
            ]
            assert Fraction(out.lo) <= min(values) <= max(values) <= Fraction(out.hi)
    for x in [I(".9", "1.1"), I(0), I("4")]:
        s = x.sqrt()
        assert Fraction(s.lo) ** 2 <= Fraction(x.lo)
        assert Fraction(s.hi) ** 2 >= Fraction(x.hi)


def test_pi_and_atan_enclosures():
    # Independent published decimal constants, with more digits than the
    # enclosure precision; not the binary64 math library as interval oracle.
    assert (
        PI.lo < Decimal("3.14159265358979323846264338327950288419716939937510") < PI.hi
    )
    ref = Decimal("0.46364760900080611621425623146121440202853705428612")
    value = atan(I(".5"))
    assert value.lo < ref < value.hi
    for x in (-3, -0.8, -0.2, 0, 0.2, 0.8, 3):
        value = atan(I(x))
        assert abs(float(value.lo) - math.atan(x)) < 2e-15
        assert float(value.hi - value.lo) < 1e-30


@pytest.mark.parametrize("precision", [7, 28, 60])
def test_taylor_remainder_exact_endpoints_and_enclosure(monkeypatch, precision):
    from decimal import localcontext
    from experiments.tracy_battery_transfer_loop.analyze import atan_small

    original = I.__init__
    remainder_endpoints = []

    def observe(self, lo, hi=None):
        original(self, lo, hi)
        if isinstance(lo, Decimal) and isinstance(hi, Decimal) and lo < 0 < hi:
            remainder_endpoints.append((lo, hi))

    monkeypatch.setattr(I, "__init__", observe)
    with localcontext() as context:
        context.prec = precision
        result = atan_small(I("0.2"))
    assert len(remainder_endpoints) == 1
    lo, hi = remainder_endpoints[0]
    assert Fraction(lo) == -Fraction(hi)
    # An independent exact-rational alternating-series enclosure of atan(1/5).
    x = Fraction(1, 5)
    lower = sum(
        ((-1) ** k * x ** (2 * k + 1) / (2 * k + 1) for k in range(70)), Fraction(0)
    )
    upper = lower + x**141 / 141
    assert Fraction(result.lo) <= lower <= upper <= Fraction(result.hi)


def test_terminal_circle_algebra():
    # Cross-check the stable cosine-bound formula against direct complex
    # terminal powers, including line charging, both orientations and voltages.
    for r, x, ch, R in [
        (0.00882, 0.0355, 0.00878, 166),
        (0.00221, 0.4115, 0.10198, 72),
    ]:
        y = 1 / complex(r, x)
        A = np.conj(y) - 1j * ch / 2
        C = A * y
        for u, v, d in product([0.94, 1, 1.06], [0.94, 1, 1.06], [-0.2, 0, 0.2]):
            power = u * np.conj(y * (u - v * np.exp(-1j * d)) + 1j * ch * u / 2)
            k = (
                1
                + (u - v) ** 2 / (2 * u * v)
                + (abs(A) ** 2 - abs(C)) * u / (2 * abs(C) * v)
            )
            k += (abs(y) ** 2 - abs(C)) * v / (2 * abs(C) * u) - (R / 100) ** 2 / (
                2 * abs(C) * u**3 * v
            )
            direct = abs(power) ** 2 - (R / 100) ** 2
            cosine = 2 * abs(C) * u**3 * v * (k - math.cos(np.angle(C) - d))
            assert abs(direct - cosine) < 1e-11


@pytest.fixture(scope="module")
def source():
    original = cp.Problem.solve
    cp.Problem.solve = forbid_solves
    yield transfer.source_inventory(), e3_prepared.verified_inputs()
    cp.Problem.solve = original


@pytest.mark.parametrize("n", [2, 3, 4])
def test_complete_certificate_and_partition(n, source):
    build, kwargs, powers, *_ = model.construct(n, *source)
    proof = prove_loop(kwargs, powers[0])
    assert build.prob.status is None and proof["proved_exclusion"]
    assert proof["unresolved_boxes"] == 0 and proof["other_excluded_leaves"] == 0
    boxes = [
        [[Fraction(Decimal(x)) for x in pair] for pair in leaf["voltage_boxes"]]
        for leaf in proof["certificate_leaves"]
    ]

    def volume(box):
        result = Fraction(1)
        for lo, hi in box:
            result *= hi - lo
        return result

    vlo, vhi = map(lambda x: Fraction(Decimal(x)), proof["voltage_range"])
    assert sum(map(volume, boxes)) == (vhi - vlo) ** 4
    for a, b in combinations(boxes, 2):
        assert any(min(x[1], y[1]) <= max(x[0], y[0]) for x, y in zip(a, b))
    for leaf in proof["certificate_leaves"]:
        assert Decimal(leaf["margin_radians"]) > 0
        assert all(
            vlo <= lo <= hi <= vhi
            for lo, hi in [
                [Fraction(Decimal(x)) for x in pair] for pair in leaf["voltage_boxes"]
            ]
        )


def test_accepted_control_not_excluded(source):
    build, kwargs, powers, *_ = model.construct(1, *source)
    proof = prove_loop(kwargs, powers[0], max_boxes=1)
    assert not proof["proved_exclusion"] and build.prob.status is None
    # Restrict only the analysis box to the retained accepted voltage point;
    # no OPF constraint or archived point is modified and no solve is called.
    result = transfer.read(model.HERE / "results/slice_001/call-001/result.json.gz")[
        "result"
    ]
    case = {
        k: np.array(v, copy=True) if isinstance(v, np.ndarray) else v
        for k, v in kwargs["case"].items()
    }
    for row in case["bus"]:
        if row[0] in (24, 70, 71, 72):
            v = float(np.asarray(result["Vm"])[0, int(row[0]) - 1])
            row[11] = row[12] = v
    # The accepted control must satisfy the derived power/angle bounds at its
    # actual voltages; the unsplit singleton is not excluded.
    point_check = prove_loop(dict(kwargs, case=case), powers[0], max_boxes=1)
    assert not point_check["proved_exclusion"]


def test_slack_definition():
    point = dict(
        w=[[1.0, 1.0]], W_re=[[0.8]], W_im=[[0.0]], voltage_product_pairs=[[0, 1]]
    )
    metrics, gap, rel, *_ = slack_metrics(point)
    assert metrics["max_relative"] == pytest.approx(0.36)
    assert gap[0, 0] == pytest.approx(0.36) and rel[0, 0] == pytest.approx(0.36)
