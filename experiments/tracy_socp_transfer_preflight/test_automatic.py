"""Automatic topology/cut discovery and conservative non-solving regressions."""

import copy
from dataclasses import replace
from decimal import Decimal, localcontext
from fractions import Fraction
from itertools import combinations
import json
from pathlib import Path

import numpy as np
import pytest

from cvxopf import DispatchableGenerator, Load, OPFOptions, StorageUnitIdeal
from experiments.tracy_socp_transfer_preflight import preflight as p
from experiments.tracy_socp_transfer_preflight import automatic_loops as a
from experiments.tracy_socp_transfer_preflight.historical_replay import (
    inventory_matches,
)


@pytest.fixture(scope="module")
def retained():
    from experiments.tracy_battery_transfer_slice import model as s

    with p.no_optimization():
        data = p.transfer.source_inventory()
        prepared = p.e3_prepared.verified_inputs()
        _, kwargs, _, _, _, _ = s.construct(2, data, prepared)
    case = s.source_case(data)
    powers = {
        name: np.asarray(case[name]["result"]["b"], float)[13]
        for name in ("ac", "socp", "lossy_dc", "singlenode_dc")
    }
    return kwargs, powers


def exclusions(scan):
    return [r for r in scan["loops"] if r["status"] == "certified_incompatible"]


@pytest.mark.parametrize("donor", ["socp", "lossy_dc", "singlenode_dc"])
def test_known_loop_is_discovered_and_entire_voltage_box_excluded(retained, donor):
    kwargs, pins = retained
    with p.no_optimization():
        scanner = a.LoopScanner(kwargs)
        result = scanner.scan_hour(pins[donor])
    assert len(scanner.cycles) == 51
    proofs = exclusions(result)
    assert [r["cycle"] for r in proofs] == [[24, 70, 71, 72]]
    proof = proofs[0]
    assert proof["unresolved_boxes"] == 0
    assert any(e["cut_buses"] == [71, 73] for e in proof["necessary_exports"])
    assert any(e["cut_buses"] == [71, 72, 73] for e in proof["necessary_exports"])
    boxes = [
        [[(Fraction(Decimal(x))) for x in pair] for pair in leaf["voltage_boxes"]]
        for leaf in proof["certificate_leaves"]
    ]

    def volume(box):
        result = Fraction(1)
        for lo, hi in box:
            result *= hi - lo
        return result

    root = [
        [Fraction(scanner.voltage[n].lo), Fraction(scanner.voltage[n].hi)]
        for n in proof["voltage_bus_order"]
    ]
    assert sum(map(volume, boxes)) == volume(root)
    for left, right in combinations(boxes, 2):
        assert any(min(x[1], y[1]) <= max(x[0], y[0]) for x, y in zip(left, right))
    for leaf in proof["certificate_leaves"]:
        if leaf["reason"] == "cycle_nonclosure":
            assert Decimal(leaf["winding_gap_radians"]) > 0


def test_bus_relabel_row_permutation_and_branch_reversal(retained):
    kwargs, pins = retained
    changed = copy.deepcopy(kwargs)
    mapping = {
        int(r[0]): 10000 + 17 * (len(kwargs["case"]["bus"]) - j)
        for j, r in enumerate(kwargs["case"]["bus"])
    }
    for row in changed["case"]["bus"]:
        row[0] = mapping[int(row[0])]
    changed["case"]["bus"] = changed["case"]["bus"][::-1].copy()
    for row in changed["case"]["branch"]:
        row[0], row[1] = mapping[int(row[1])], mapping[int(row[0])]
    changed["case"]["branch"] = changed["case"]["branch"][::-1].copy()
    for key in ("generators", "loads", "storage", "nondispatchable"):
        changed[key] = [replace(unit, bus=mapping[unit.bus]) for unit in changed[key]]
    with p.no_optimization():
        scanner = a.LoopScanner(changed)
        proofs = exclusions(scanner.scan_hour(pins["socp"]))
    inverse = {v: k for k, v in mapping.items()}
    assert [set(inverse[n] for n in r["cycle"]) for r in proofs] == [{24, 70, 71, 72}]
    assert any(
        set(inverse[n] for n in e["cut_buses"]) == {71, 73}
        for e in proofs[0]["necessary_exports"]
    )


def triangle(pin=250):
    buses = np.zeros((3, 13))
    buses[:, 0] = [101, 305, 902]
    buses[:, 11:13] = [1.06, 0.94]
    branches = np.zeros((3, 13))
    branches[:, :2] = [[101, 305], [305, 902], [902, 101]]
    branches[:, 2:6] = [0.01, 0.1, 0.01, 120]
    branches[:, 10] = 1
    return dict(
        case=dict(bus=buses, branch=branches, baseMVA=100),
        T=1,
        formulation="ac",
        options=OPFOptions(),
        generators=[
            DispatchableGenerator(
                305, p_max_mw=1000, p_min_mw=-1000, q_max_mvar=1000, q_min_mvar=-1000
            )
        ],
        loads=[],
        storage=[
            StorageUnitIdeal(
                101, apparent_power_rating=1000, capacity=1000, initial_soc=500
            )
        ],
    ), np.array([pin])


def test_other_topology_no_bus_numbers_or_named_proof(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("named loop checker used")

    monkeypatch.setattr(p.loop, "prove_loop", forbidden)
    kwargs, pins = triangle(300)
    with p.no_optimization():
        proof = exclusions(a.LoopScanner(kwargs).scan_hour(pins))
    assert len(proof) == 1 and proof[0]["cycle"] == [101, 305, 902]
    assert proof[0]["nonclosure_leaves"] == 0  # empty edge intersection suffices


def test_feasible_rank_one_witness_not_excluded_when_export_triggered():
    kwargs, _ = triangle()
    voltage = np.exp(1j * np.array([0.1, 0, 0.02]))
    nodal = np.zeros(3, complex)
    for row in kwargs["case"]["branch"]:
        ids = {101: 0, 305: 1, 902: 2}
        i, j = (ids[int(x)] for x in row[:2])
        y = 1 / complex(row[2], row[3])
        for start, end in ((i, j), (j, i)):
            s = (
                100
                * voltage[start]
                * np.conj(
                    y * (voltage[start] - voltage[end]) + 0.5j * row[4] * voltage[start]
                )
            )
            assert abs(s) < row[5]
            nodal[start] += s
    kwargs["storage"] = []
    # Widen fixed dispatch by binary64 rounding error of this independent
    # witness reconstruction; this is only a synthetic fixture, not a gate.
    kwargs["generators"] = [
        DispatchableGenerator(
            int(row[0]),
            p_min_mw=float(s.real - 1e-10),
            p_max_mw=float(s.real + 1e-10),
            q_min_mvar=float(s.imag - 1e-10),
            q_max_mvar=float(s.imag + 1e-10),
        )
        for row, s in zip(kwargs["case"]["bus"], nodal)
    ]
    scanner = a.LoopScanner(kwargs, max_boxes=1)
    result = scanner.scan_hour(np.array([]))
    assert result["loops"][0]["status"] == "unresolved_budget"
    assert not exclusions(result)


@pytest.mark.parametrize(
    "mode", ["unrated", "tap", "shift", "parallel", "hvdc", "large_arc", "limits_off"]
)
def test_unsupported_never_fabricates_a_certificate(mode):
    kwargs, pins = triangle(300)
    if mode == "unrated":
        kwargs["case"]["branch"][0, 5] = 0
    elif mode == "tap":
        kwargs["case"]["branch"][0, 8] = 1.1
    elif mode == "shift":
        kwargs["case"]["branch"][0, 9] = 10
    elif mode == "parallel":
        kwargs["case"]["branch"] = np.vstack(
            [kwargs["case"]["branch"], kwargs["case"]["branch"][0]]
        )
    elif mode == "hvdc":
        kwargs["hvdc"] = [object()]
    elif mode == "large_arc":
        kwargs["case"]["branch"][:, 5] = 10000
    elif mode == "limits_off":
        kwargs["options"] = replace(kwargs["options"], enforce_branch_limits=False)
    result = a.LoopScanner(kwargs).scan_hour(pins)
    assert result["status"] == "unresolved"
    assert result["loops"][0]["status"] == "unsupported"


def test_signed_shunts_and_multiple_signed_devices_in_cut():
    kwargs, pins = triangle(300)
    kwargs["case"]["bus"][0, 4] = -5
    kwargs["generators"] += [DispatchableGenerator(101, p_min_mw=-3, p_max_mw=5)]
    kwargs["loads"] = [Load(101, -7, "injection"), Load(101, 2, "demand")]
    values = a.injections(kwargs, pins, 0)
    assert values[101][0].lo == 302
    scanner = a.LoopScanner(kwargs)
    losses = {
        i: a.I((scanner.base * e["r"] * a.square(scanner.current_cap(i, values))).hi)
        for i, e in scanner.geometry.items()
    }
    bound = scanner.export_bounds(scanner.cycles[0], values, losses)[101, 305]
    # Leaf-free singleton cut: 302 - max(-5*V²) - other rating.
    expected = a.I(302) - a.I((a.I(-5) * a.square(scanner.voltage[101])).hi) - 120
    assert bound[0] >= expected.lo
    assert Decimal(bound[3][1]) < 0


@pytest.mark.parametrize("precision", [7, 28, 60])
def test_winding_test_preserves_zero_and_nonzero_integer_windings(precision):
    with localcontext() as context:
        context.prec = precision
        assert a.winding_gap(a.I("-.01", ".01")) is None
        for k in (-2, -1, 1, 2):
            assert a.winding_gap(k * (2 * a.PI)) is None
        assert a.winding_gap(a.I(".01", ".02")) > 0
        assert a.winding_gap(a.I("-.02", "-.01")) > 0


def test_no_trigger_is_unknown_and_inputs_unchanged(retained):
    kwargs, pins = retained
    before = p.transfer.object_hash(p.original.q.serializable(kwargs["case"]))
    with p.no_optimization():
        scanner = a.LoopScanner(kwargs, max_boxes=1)
        result = scanner.scan_schedule(pins["ac"][None, :], 2452)
    assert result["verdict"] == "unresolved"
    assert len(result["hours"][0]["loop_status_codes"]) == len(scanner.cycles)
    assert before == p.transfer.object_hash(p.original.q.serializable(kwargs["case"]))


def test_selected_retained_backtest_hashes_and_witnesses():
    path = p.HERE / "AUTOMATIC_EVIDENCE.json"
    if not path.exists():
        pytest.skip("selected automatic evidence not saved yet")
    report = json.loads(path.read_text())
    assert report["sources"]["scanner_sha256"] == p.transfer.sha(Path(a.__file__))
    from experiments.tracy_socp_transfer_preflight import automatic

    assert report["sources"]["backtest_sha256"] == p.transfer.sha(
        Path(automatic.__file__)
    )
    assert report["sources"]["protocol_sha256"] == p.transfer.sha(
        p.HERE / "AUTOMATIC_PROTOCOL.md"
    )
    assert inventory_matches(
        report["sources"]["frozen_checkpoints"], automatic.frozen_checkpoints()
    )
    assert report["historical_bytes_unchanged"]
    assert report["policy"]["new_optimizer_calls"] == 0
    assert report["summary"]["historical_outcomes"] == {
        "compatible": 15,
        "native_rejection": 2,
        "timeout": 1,
    }
    assert report["summary"]["compatible_witness_contradictions"] == 0
    assert len(report["ac_controls"]) == 6
    assert all(c["verdict"] == "unresolved" for c in report["ac_controls"])
    for row in report["transfers"]:
        certified = [
            h["hour"] for h in row["hours"] if h["status"] == "certified_incompatible"
        ]
        expected = (
            [2452, 2453]
            if row["job"]["id"] == 13
            else ([2452] if row["job"]["id"] in (14, 15) else [])
        )
        assert certified == expected


def test_new_hour_partition_is_complete_with_exact_rational_volumes():
    path = p.HERE / "AUTOMATIC_EVIDENCE.json"
    if not path.exists():
        pytest.skip("selected automatic evidence not saved yet")
    report = json.loads(path.read_text())
    hour = next(h for h in report["transfers"][12]["hours"] if h["hour"] == 2453)
    proof = exclusions(hour)[0]
    assert proof["tested_boxes"] == 9 and proof["excluded_leaves"] == 5
    boxes = [
        [[(Fraction(Decimal(x))) for x in pair] for pair in leaf["voltage_boxes"]]
        for leaf in proof["certificate_leaves"]
    ]

    def volume(box):
        result = Fraction(1)
        for lo, hi in box:
            result *= hi - lo
        return result

    root = [
        [min(b[j][0] for b in boxes), max(b[j][1] for b in boxes)] for j in range(4)
    ]
    assert sum(map(volume, boxes)) == volume(root)
    for left, right in combinations(boxes, 2):
        assert any(min(x[1], y[1]) <= max(x[0], y[0]) for x, y in zip(left, right))
    assert all(
        a.winding_gap(a.I(*(Decimal(x) for x in leaf["closure_interval_radians"]))) > 0
        for leaf in proof["certificate_leaves"]
    )


def test_angle_bounds_enclose_independent_physical_branch_points():
    from itertools import product

    kwargs, _ = triangle()
    scanner = a.LoopScanner(kwargs)
    row, edge = scanner.rows[0], scanner.geometry[0]
    y = 1 / complex(row[2], row[3])
    eligible = 0
    for u, v, delta in product((0.94, 1, 1.06), repeat=3):
        # Delta grid: chosen independently of computed interval bounds.
        delta = (delta - 1) * 2
        left, right = complex(u), v * np.exp(-1j * delta)
        forward = 100 * left * np.conj(y * (left - right) + 0.5j * row[4] * left)
        reverse = 100 * right * np.conj(y * (right - left) + 0.5j * row[4] * right)
        if max(abs(forward), abs(reverse)) > row[5]:
            continue
        eligible += 1
        ub, vb = (Decimal.from_float(u),) * 2, (Decimal.from_float(v),) * 2
        # Safe outward allowance for floating-point power reconstruction, not
        # a certificate tolerance or a physical-model modification.
        bound = (Decimal.from_float(float(forward.real - 1e-10)),)
        lo = scanner.power_lower(0, ub, vb, bound)
        hi = scanner.thermal_upper(0, ub, vb)
        assert float(lo) <= delta + 1e-13 and delta <= float(hi) + 1e-13
        assert delta >= -float(scanner.thermal_upper(0, vb, ub)) - 1e-13
    assert eligible > 5 and edge["r"].lo > 0


def test_cycle_length_coverage_is_configurable_not_inferred_from_no_certificate():
    kwargs, pins = triangle(0)
    rows = np.zeros((5, 13))
    rows[:, 0], rows[:, 11], rows[:, 12] = [101, 305, 902, 1100, 2222], 1.06, 0.94
    kwargs["case"]["bus"] = rows
    branches = np.zeros((5, 13))
    branches[:, :2] = [[101, 305], [305, 902], [902, 1100], [1100, 2222], [2222, 101]]
    branches[:, 2:6], branches[:, 10] = [0.01, 0.1, 0.01, 120], 1
    kwargs["case"]["branch"] = branches
    assert a.LoopScanner(kwargs).scan_hour(pins)["status"] == "unresolved"
    assert a.LoopScanner(kwargs).cycles == []
    assert len(a.LoopScanner(kwargs, max_length=5).cycles) == 1
