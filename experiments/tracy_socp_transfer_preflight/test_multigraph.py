"""Independent no-solve transformer/parallel circuit and retained regressions."""

import copy
from decimal import Decimal, localcontext
from fractions import Fraction
import json
from pathlib import Path

import numpy as np
import pytest

from cvxopf import DispatchableGenerator
from experiments.tracy_socp_transfer_preflight import preflight as p
from experiments.tracy_socp_transfer_preflight import multigraph_loops as m
from experiments.tracy_socp_transfer_preflight.historical_replay import (
    inventory_matches,
)
from experiments.tracy_socp_transfer_preflight.test_automatic import triangle


def direct(row, vf, vt, base=100):
    """Independent MATPOWER terminal-admittance evaluation, not interval code."""
    y = 1 / complex(row[2], row[3])
    charging = 0.5j * row[4]
    tap = (row[8] or 1) * np.exp(1j * np.deg2rad(row[9]))
    current_f = (y + charging) / abs(tap) ** 2 * vf - y / tap.conjugate() * vt
    current_t = -y / tap * vf + (y + charging) * vt
    return base * vf * current_f.conjugate(), base * vt * current_t.conjugate()


def witness_kwargs(kwargs, voltages):
    kwargs = copy.deepcopy(kwargs)
    ids = {int(row[0]): j for j, row in enumerate(kwargs["case"]["bus"])}
    nodal = np.zeros(len(ids), complex)
    for row in kwargs["case"]["branch"]:
        if row[10] <= 0:
            continue
        i, j = (ids[int(x)] for x in row[:2])
        sf, st = direct(row, voltages[i], voltages[j])
        assert max(abs(sf), abs(st)) < row[5]
        nodal[i] += sf
        nodal[j] += st
    kwargs["storage"] = []
    kwargs["generators"] = [
        DispatchableGenerator(
            bus=int(row[0]),
            p_min_mw=float(s.real - 1e-9),
            p_max_mw=float(s.real + 1e-9),
            q_min_mvar=float(s.imag - 1e-9),
            q_max_mvar=float(s.imag + 1e-9),
        )
        for row, s in zip(kwargs["case"]["bus"], nodal)
    ]
    return kwargs


@pytest.mark.parametrize(
    "tap,shift",
    [(1, 0), (0.93, 10), (1.06, -17), (0.98, 179), (1.02, -179), (1.02, 370)],
)
def test_terminal_coordinate_identity_and_enclosed_angles_losses(tap, shift):
    kwargs, _ = triangle(0)
    row = kwargs["case"]["branch"][0]
    row[8:10] = [tap, shift]
    scanner = m.LoopScanner(kwargs)
    enclosed = 0
    for u, v, electrical_delta in [
        (1, 1, 0.02),
        (0.98, 1.02, -0.04),
        (1.06, 0.94, 0.05),
    ]:
        phase = np.deg2rad(shift)
        vf, vt = u * np.exp(1j * (phase + electrical_delta)), complex(v)
        sf, st = direct(row, vf, vt)
        transformed = vf / (tap * np.exp(1j * phase))
        y = 1 / complex(row[2], row[3])
        internal_sf = (
            100
            * transformed
            * np.conj(y * (transformed - vt) + 0.5j * row[4] * transformed)
        )
        internal_st = 100 * vt * np.conj(y * (vt - transformed) + 0.5j * row[4] * vt)
        assert abs(sf - internal_sf) < 1e-10 and abs(st - internal_st) < 1e-10
        physical_loss = sf.real + st.real
        exact_loss = 100 * row[2] * abs(y * (transformed - vt)) ** 2
        assert physical_loss == pytest.approx(exact_loss, abs=1e-10)
        if max(abs(sf), abs(st)) >= row[5]:
            continue
        enclosed += 1
        ubox = (Decimal.from_float(u),) * 2
        vbox = (Decimal.from_float(v),) * 2
        forward, reverse = (
            (Decimal.from_float(float(sf.real - 1e-9)),),
            (Decimal.from_float(float(st.real - 1e-9)),),
        )
        interval, _ = scanner.branch_angle(0, 101, 305, ubox, vbox, forward, reverse)
        reversed_interval, _ = scanner.branch_angle(
            0, 305, 101, vbox, ubox, reverse, forward
        )
        actual = m.I(
            float(phase + electrical_delta - 1e-12),
            float(phase + electrical_delta + 1e-12),
        )
        assert interval is not None and m.intersect_modulo(actual, interval) is not None
        assert (
            reversed_interval is not None
            and m.intersect_modulo(-actual, reversed_interval) is not None
        )
        bound = (
            scanner.base
            * scanner.geometry[0]["r"]
            * m.v1.square(scanner.current_cap(0))
        ).hi
        assert float(bound) >= physical_loss - 1e-10
    assert enclosed > 0


def test_reversing_a_transformer_with_equivalent_zero_charging_circuit():
    kwargs, _ = triangle(0)
    row = kwargs["case"]["branch"][0]
    row[4], row[8], row[9] = 0, 0.97, 12
    reverse = row.copy()
    reverse[:2] = row[1::-1]
    reverse[2:4] *= row[8] ** 2
    reverse[8], reverse[9] = 1 / row[8], -row[9]
    vf, vt = np.exp(0.24j), 1.01 * np.exp(0.02j)
    sf, st = direct(row, vf, vt)
    rsf, rst = direct(reverse, vt, vf)
    assert sf == pytest.approx(rst, abs=1e-10) and st == pytest.approx(rsf, abs=1e-10)


def test_parallel_export_is_not_assigned_to_each_member():
    kwargs, pins = triangle(300)
    kwargs["case"]["branch"] = np.vstack(
        [kwargs["case"]["branch"], kwargs["case"]["branch"][0]]
    )
    scanner = m.LoopScanner(kwargs)
    assert len(scanner.cycles) == 2 and (101, 305) in scanner.cycles
    values = m.v1.injections(kwargs, pins, 0)
    losses = {
        i: m.I((scanner.base * e["r"] * m.v1.square(scanner.current_cap(i, values))).hi)
        for i, e in scanner.geometry.items()
    }
    group = scanner.export_bounds((101, 305, 902), values, losses)[101, 305]
    assert group[0] == 180
    assert scanner.branch_bound(0, group)[0] == 60
    assert scanner.branch_bound(3, group)[0] == 60
    # Every internal parallel loss appears in the cut; no equivalent impedance.
    assert scanner.pairs[(101, 305)] == [0, 3]


def test_unequal_reversed_parallel_transformer_feasible_witness_not_excluded():
    kwargs, _ = triangle(0)
    second = kwargs["case"]["branch"][0].copy()
    second[:2] = second[1::-1]
    second[2:6] = [0.017, 0.15, 0.008, 90]
    second[8:10] = [0.99, -2]
    kwargs["case"]["branch"][0, 8:10] = [1.01, 3]
    kwargs["case"]["branch"] = np.vstack([kwargs["case"]["branch"], second])
    voltage = np.exp(1j * np.array([0.14, 0, 0.03]))
    kwargs = witness_kwargs(kwargs, voltage)
    with p.no_optimization():
        scanner = m.LoopScanner(kwargs, max_boxes=1)
        result = scanner.scan_schedule(np.empty((1, 0)))
    assert result["verdict"] == "unresolved" and not result["unsupported_cycles"]
    assert result["parallel_corridors"] == 1
    assert "B" in result["hours"][0]["loop_status_codes"]


def test_parallel_phase_conflict_is_detected_without_export_trigger():
    kwargs, pins = triangle(0)
    kwargs["case"]["branch"] = kwargs["case"]["branch"][:1].copy()
    kwargs["case"]["branch"][0, 5] = 20
    second = kwargs["case"]["branch"][0].copy()
    second[9] = 30
    kwargs["case"]["branch"] = np.vstack([kwargs["case"]["branch"], second])
    proof = m.LoopScanner(kwargs).scan_hour(pins)
    assert proof["status"] == "certified_incompatible"
    leaf = proof["loops"][0]["certificate_leaves"][0]
    assert leaf["reason"] == "empty_edge_angle"
    assert (
        leaf["branch_details"][-1]["reason"]
        == "parallel_branch_angle_conflict_modulo_2pi"
    )


def test_loop_phase_shift_nonclosure_is_detected_without_export():
    kwargs, pins = triangle(0)
    kwargs["case"]["branch"][:, 5] = 20
    kwargs["case"]["branch"][:, 9] = 10
    result = m.LoopScanner(kwargs).scan_hour(pins)
    assert result["status"] == "certified_incompatible"
    assert result["loops"][0]["nonclosure_leaves"] == 1


def test_phase_wrap_does_not_create_a_false_parallel_conflict():
    kwargs, _ = triangle(0)
    kwargs["case"]["branch"] = kwargs["case"]["branch"][:1].copy()
    kwargs["case"]["branch"][0, 5:6] = 60
    kwargs["case"]["branch"][0, 9] = 179
    second = kwargs["case"]["branch"][0].copy()
    second[9] = -179
    kwargs["case"]["branch"] = np.vstack([kwargs["case"]["branch"], second])
    kwargs = witness_kwargs(kwargs, np.array([-1 + 0j, 1 + 0j, 1 + 0j]))
    result = m.LoopScanner(kwargs, max_boxes=1).scan_hour(np.array([]))
    assert (
        result["status"] == "unresolved"
        and result["loops"][0]["status"] != "unsupported"
    )


@pytest.mark.parametrize("tap", [-1, np.nan, np.inf])
def test_invalid_transformers_are_not_guessed(tap):
    kwargs, pins = triangle(0)
    kwargs["case"]["branch"][0, 8] = tap
    assert m.LoopScanner(kwargs).scan_hour(pins)["loops"][0]["status"] == "unsupported"


def test_leaf_transformer_bound_uses_physical_shunts_and_internal_voltage():
    kwargs, _ = triangle(0)
    kwargs["case"]["branch"] = kwargs["case"]["branch"][:1].copy()
    kwargs["case"]["branch"][0, 8:10] = [1.05, 4]
    kwargs["case"]["bus"][0, 4:6] = [3, -2]
    scanner = m.LoopScanner(kwargs)
    values = {
        101: [m.I(20), m.I(-10)],
        305: [m.I(-100, 100), m.I(-100, 100)],
        902: [m.I(0), m.I(0)],
    }
    u = scanner.internal_voltage(0, 101)
    physical = scanner.voltage[101]
    pbox = values[101][0] - 3 * m.v1.square(physical)
    qbox = values[101][1] - 2 * m.v1.square(physical)
    terminal = (
        m.v1.square(m.I(m.v1.magnitude(pbox))) + m.v1.square(m.I(m.v1.magnitude(qbox)))
    ).sqrt()
    bound = terminal / (scanner.base * m.I(u.lo)) + scanner.geometry[0]["c"] * m.I(u.hi)
    assert scanner.current_cap(0, values).hi <= bound.hi


def test_retained_selected_checkpoint_covers_all_eight_and_preserves_witnesses():
    path = p.HERE / "MULTIGRAPH_EVIDENCE.json"
    if not path.exists():
        pytest.skip("selected multigraph evidence not yet saved")
    from experiments.tracy_socp_transfer_preflight import multigraph

    result = json.loads(path.read_text())
    assert result["sources"]["scanner_sha256"] == p.transfer.sha(Path(m.__file__))
    assert result["sources"]["backtest_sha256"] == p.transfer.sha(
        Path(multigraph.__file__)
    )
    assert inventory_matches(
        result["sources"]["frozen_checkpoints"], multigraph.frozen_checkpoints()
    )
    assert (
        result["historical_bytes_unchanged"]
        and result["policy"]["new_optimizer_calls"] == 0
    )
    summary = result["summary"]
    assert summary["historical_outcomes"] == {
        "compatible": 15,
        "native_rejection": 2,
        "timeout": 1,
    }
    assert (
        summary["compatible_witness_contradictions"]
        == summary["control_contradictions"]
        == 0
    )
    assert summary["simple_cycles_per_schedule"] == [51]
    assert summary["parallel_corridors_per_schedule"] == [7]
    assert summary["loop_hour_statuses"]["unsupported"] == 0
    for row in result["transfers"]:
        assert len(row["formerly_unsupported_coverage"]) == 8
        assert not row["unsupported_cycles"]
        expected = (
            [2452, 2453]
            if row["job"]["id"] == 13
            else ([2452] if row["job"]["id"] in (14, 15) else [])
        )
        assert [
            h["hour"] for h in row["hours"] if h["status"] == "certified_incompatible"
        ] == expected
        for hour in row["hours"]:
            assert len(hour["loop_status_codes"]) == 58
            for proof in hour["loops"]:
                if proof["status"] != "certified_incompatible":
                    continue
                boxes = [
                    [
                        [(Fraction(Decimal(x))) for x in pair]
                        for pair in leaf["voltage_boxes"]
                    ]
                    for leaf in proof["certificate_leaves"]
                ]

                def volume(box):
                    result = Fraction(1)
                    for lo, hi in box:
                        result *= hi - lo
                    return result

                root = [
                    [min(b[j][0] for b in boxes), max(b[j][1] for b in boxes)]
                    for j in range(len(proof["cycle"]))
                ]
                assert sum(map(volume, boxes)) == volume(root)


def test_unit_tap_v1_bound_compatibility():
    kwargs, pins = triangle(0)
    older, newer = m.v1.LoopScanner(kwargs), m.LoopScanner(kwargs)
    assert newer.arc(0) == older.arc(0)
    assert newer.current_cap(0).hi == older.current_cap(0).hi
    u = (Decimal.from_float(0.94), Decimal.from_float(1.06))
    assert newer.thermal_upper(0, u, u) == older.thermal_upper(0, u, u)
    before = copy.deepcopy(kwargs["case"]["branch"])
    newer.scan_hour(pins)
    assert np.array_equal(before, kwargs["case"]["branch"])


def test_transformer_voltage_mismatch_is_detected_without_power_trigger():
    kwargs, pins = triangle(0)
    kwargs["case"]["branch"][0, 8] = 1.4
    kwargs["case"]["branch"][0, 5] = 20
    result = m.LoopScanner(kwargs).scan_hour(pins)
    assert result["status"] == "certified_incompatible"
    assert result["loops"][0]["certificate_leaves"][0]["reason"] == "empty_edge_angle"


@pytest.mark.parametrize("mode", ["unrated", "large_arc", "hvdc"])
def test_remaining_assumptions_stay_explicitly_unsupported(mode):
    kwargs, pins = triangle(0)
    if mode == "unrated":
        kwargs["case"]["branch"][0, 5] = 0
    elif mode == "large_arc":
        kwargs["case"]["branch"][0, 5] = 10000
    else:
        kwargs["hvdc"] = [object()]
    result = m.LoopScanner(kwargs).scan_hour(pins)
    assert result["status"] == "unresolved"
    assert result["loops"][0]["status"] == "unsupported"


@pytest.mark.parametrize("precision", [7, 28, 60])
def test_phase_normalization_and_modular_alignment_ignore_ambient_precision(precision):
    kwargs, _ = triangle(0)
    kwargs["case"]["branch"][0, 9] = 370.2
    expected = Fraction.from_float(370.2) - 360
    with localcontext() as ctx:
        ctx.prec = precision
        scanner = m.LoopScanner(kwargs)
        degrees = scanner.geometry[0]["shift"] * 180 / m.PI
        assert Fraction(degrees.lo) <= expected <= Fraction(degrees.hi)
        anchor = m.I("3.12", "3.16")
        other = anchor - 2 * m.PI
        intersection = m.intersect_modulo(anchor, other)
        assert (
            intersection is not None
            and intersection.lo <= anchor.lo <= anchor.hi <= intersection.hi
        )
