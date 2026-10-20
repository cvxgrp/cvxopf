"""M11 Gate A: lifted maps agree with direct complex-voltage physics.

These are algebraic checks, not optimization or exactness claims. They use
public/synthetic cases and arbitrary voltage trajectories, not private data.
"""

import cvxpy as cp
import numpy as np
import pytest
from scipy import sparse

from cvxopf._voltage_product import make_voltage_product_maps
from cvxopf.data import validate_case
from cvxopf.network import (
    make_branch_admittance,
    make_ybus_matpower,
    reindex_case_to_consecutive,
)
from cvxopf.testcases import case9, case14


def _maps(case):
    validate_case(case)
    indexed, mapping = reindex_case_to_consecutive(case)
    admittance = make_branch_admittance(indexed)
    ybus = make_ybus_matpower(indexed, branch_admittance=admittance)
    return indexed, mapping, admittance, ybus, make_voltage_product_maps(ybus, admittance)


def _lift(maps, voltage):
    products = voltage[maps.pairs[:, 0]] * voltage[maps.pairs[:, 1]].conj()
    return np.concatenate((abs(voltage) ** 2, products.real, products.imag), axis=0)


def _power(power_map, lifted):
    return power_map.p @ lifted + 1j * (power_map.q @ lifted)


def _assert_direct_physics(case, horizon=4):
    indexed, _, a, ybus, maps = _maps(case)
    rng = np.random.default_rng(20261001)
    voltage = rng.uniform(0.9, 1.1, (maps.nb, horizon)) * np.exp(
        1j * rng.uniform(-0.4, 0.4, (maps.nb, horizon))
    )
    lifted = _lift(maps, voltage)
    # Direct currents and S = V conj(I), independent of the sparse map algebra.
    nodal = voltage * (ybus @ voltage).conj()
    vf, vt = voltage[a.from_bus], voltage[a.to_bus]
    sf = vf * (a.yff[:, None] * vf + a.yft[:, None] * vt).conj()
    st = vt * (a.ytf[:, None] * vf + a.ytt[:, None] * vt).conj()
    for actual, expected in (
        (_power(maps.nodal, lifted), nodal),
        (_power(maps.branch_from, lifted), sf),
        (_power(maps.branch_to, lifted), st),
    ):
        np.testing.assert_allclose(actual, expected, atol=3e-14, rtol=3e-13)

    # Sum of bus injections = branch terminal sums + bus shunt withdrawal.
    shunt = (indexed["bus"][:, 4] - 1j * indexed["bus"][:, 5]) / indexed["baseMVA"]
    np.testing.assert_allclose(
        _power(maps.nodal, lifted).sum(axis=0),
        (_power(maps.branch_from, lifted) + _power(maps.branch_to, lifted)).sum(axis=0)
        + (shunt[:, None] * abs(voltage) ** 2).sum(axis=0),
        atol=8e-14, rtol=3e-13,
    )
    return maps, voltage, lifted


@pytest.mark.parametrize("case_fn", [case9, case14])
@pytest.mark.parametrize("horizon", [1, 4, 24])
def test_public_cases_direct_voltage_identities(case_fn, horizon):
    _assert_direct_physics(case_fn(), horizon)


def test_transformers_shunts_parallel_reverse_rows_and_inactive_poison():
    case = case9()
    case["bus"][1, 4:6] = [3.1, -2.2]
    case["branch"][0, 2:5] = [0.02, 0.18, 0.06]
    case["branch"][0, 8:10] = [1.07, 13.0]
    reverse = case["branch"][[0]].copy()
    reverse[0, :2] = reverse[0, [1, 0]]
    reverse[0, 8:10] = [0.94, -21.0]
    inactive = case["branch"][[1]].copy()
    inactive[0, [2, 3, 4, 8, 9]] = np.nan
    inactive[0, 10] = 0
    case["branch"] = np.vstack((case["branch"], reverse, reverse, inactive))
    maps, _, lifted = _assert_direct_physics(case)
    assert maps.branch_pair[0] == maps.branch_pair[-3] == maps.branch_pair[-2]
    assert maps.branch_orientation[0] == -maps.branch_orientation[-3]
    assert maps.branch_pair[-1] == -1
    assert maps.branch_orientation[-1] == 0
    assert len(maps.pairs) == len(case9()["branch"])
    np.testing.assert_array_equal(_power(maps.branch_from, lifted)[-1], 0)
    np.testing.assert_array_equal(_power(maps.branch_to, lifted)[-1], 0)


def test_topology_pair_survives_cancelled_ybus_entries():
    case = case9()
    case["branch"] = np.repeat(case["branch"][[0]], 2, axis=0)
    case["branch"][:, 2:5] = [[0.01, 0.1, 0.0], [-0.01, -0.1, 0.0]]
    case["branch"][:, 8:10] = 0
    _, _, _, ybus, maps = _maps(case)
    np.testing.assert_array_equal(ybus, 0)
    assert maps.pairs.shape == (1, 2)
    np.testing.assert_array_equal(maps.branch_pair, [0, 0])
    maps, _, lifted = _assert_direct_physics(case)
    assert maps.nodal.p.nnz == maps.nodal.q.nnz == 0
    assert np.max(abs(_power(maps.branch_from, lifted))) > 0.1


def test_self_loop_uses_only_diagonal_product():
    case = case9()
    case["branch"] = case["branch"][[0]].copy()
    case["branch"][0, 1] = case["branch"][0, 0]
    case["branch"][0, 8:10] = [1.07, 13.0]
    maps, _, _ = _assert_direct_physics(case)
    assert maps.pairs.shape == (0, 2)
    assert maps.nodal.p.shape == (9, 9)
    np.testing.assert_array_equal(maps.branch_pair, [-1])
    np.testing.assert_array_equal(maps.branch_orientation, [0])


@pytest.mark.parametrize("mode", ["empty", "inactive"])
def test_no_active_edges_with_bus_shunts(mode):
    case = case9()
    case["bus"][2, 4:6] = [5.0, 3.0]
    if mode == "empty":
        case["branch"] = np.empty((0, 13))
    else:
        case["branch"][:, 10] = 0
        case["branch"][:, [2, 3, 4, 8, 9]] = np.nan
    maps, _, lifted = _assert_direct_physics(case)
    assert maps.pairs.shape == (0, 2)
    assert maps.nodal.p.shape == (9, 9)
    for terminal in (maps.branch_from, maps.branch_to):
        assert terminal.p.shape == (len(case["branch"]), 9)
        assert terminal.p.nnz == terminal.q.nnz == 0
        np.testing.assert_array_equal(_power(terminal, lifted), 0)


def test_disconnected_topology_needs_no_artificial_edges():
    case = case9()
    case["branch"] = case["branch"][[0, 4]].copy()
    maps, _, _ = _assert_direct_physics(case)
    assert maps.pairs.tolist() == [[0, 3], [5, 6]]


def test_external_bus_and_original_branch_row_identity():
    case = case14()
    old = case["bus"][:, 0].astype(int)
    new = 100 + 17 * np.arange(len(old))[::-1]
    remap = dict(zip(old, new, strict=True))
    case["bus"][:, 0] = new
    for table, columns in (("branch", (0, 1)), ("gen", (0,))):
        for col in columns:
            case[table][:, col] = [remap[int(bus)] for bus in case[table][:, col]]
    indexed, mapping, a, _, maps = _maps(case)
    assert mapping == {int(bus): i for i, bus in enumerate(new)}
    for row, pair in enumerate(maps.branch_pair):
        f, t = maps.pairs[pair]
        if maps.branch_orientation[row] == -1:
            f, t = t, f
        assert (f, t) == (a.from_bus[row], a.to_bus[row])
        assert tuple(new[[f, t]]) == tuple(case["branch"][row, :2])
    np.testing.assert_array_equal(indexed["bus"][:, 0], np.arange(len(old)))
    _assert_direct_physics(case)


def test_pair_order_independent_of_branch_order():
    case = case14()
    _, _, _, _, original = _maps(case)
    permutation = np.random.default_rng(11).permutation(len(case["branch"]))
    case["branch"] = case["branch"][permutation]
    _, _, _, _, shuffled = _maps(case)
    np.testing.assert_array_equal(shuffled.pairs, original.pairs)
    np.testing.assert_array_equal(shuffled.branch_pair, original.branch_pair[permutation])
    np.testing.assert_array_equal(
        shuffled.branch_orientation, original.branch_orientation[permutation]
    )


@pytest.mark.parametrize("side", ["from", "to"])
def test_both_terminal_magnitudes_use_exact_powers_and_base_mva(side):
    case = case9()
    case["baseMVA"] = 73.0
    case["branch"] = case["branch"][[0]].copy()
    case["branch"][0, 2:5] = [0.02, 0.18, 0.06]
    case["branch"][0, 8:10] = [1.15 if side == "to" else 0.85, 13.0]
    _, _, a, _, maps = _maps(case)
    voltage = np.ones((maps.nb, 1), dtype=complex)
    voltage[a.to_bus[0]] = np.exp(-0.2j)
    lifted = _lift(maps, voltage)
    sf = _power(maps.branch_from, lifted)[0, 0] * case["baseMVA"]
    st = _power(maps.branch_to, lifted)[0, 0] * case["baseMVA"]
    binding, other = (abs(sf), abs(st)) if side == "from" else (abs(st), abs(sf))
    assert binding > other
    # This rating would be active only at the named terminal for this voltage.
    # Actual cone enforcement is a builder test, not a Gate A solve.
    rating = binding
    np.testing.assert_allclose(abs((sf if side == "from" else st) / rating), 1)
    assert other / rating < 1


def test_real_sparse_maps_batch_over_time_and_lift_is_cone_tight():
    maps, voltage, lifted = _assert_direct_physics(case14(), horizon=24)
    for power_map in (maps.nodal, maps.branch_from, maps.branch_to):
        assert sparse.isspmatrix_csr(power_map.p)
        assert sparse.isspmatrix_csr(power_map.q)
        assert power_map.p.dtype.kind == power_map.q.dtype.kind == "f"
        assert power_map.p.shape[1] == maps.nb + 2 * len(maps.pairs)
    nb, ne = maps.nb, len(maps.pairs)
    i, j = maps.pairs.T
    w, c, s = lifted[:nb], lifted[nb:nb + ne], lifted[nb + ne:]
    np.testing.assert_allclose(w[i] * w[j] - c**2 - s**2, 0, atol=1e-15)
    # The intended real affine maps compose with CVXPY directly (no solve).
    x = cp.Variable(lifted.shape)
    x.value = lifted
    assert (maps.nodal.p @ x).is_affine()
    assert (maps.nodal.q @ x).is_affine()
    np.testing.assert_allclose((maps.nodal.p @ x).value, _power(maps.nodal, lifted).real)
    assert voltage.shape[1] == (maps.branch_to.p @ x).shape[1]
    assert maps.nodal.p.nnz <= nb + 4 * ne
    assert maps.nodal.q.nnz <= nb + 4 * ne


def test_inconsistent_ybus_topology_rejected():
    _, _, a, ybus, _ = _maps(case9())
    ybus[0, 1] = 1j  # No active branch connects these buses.
    with pytest.raises(ValueError, match="outside active topology"):
        make_voltage_product_maps(ybus, a)


@pytest.mark.parametrize("ybus", [np.zeros((2, 3)), np.full((9, 9), np.nan)])
def test_invalid_ybus_rejected(ybus):
    _, _, a, _, _ = _maps(case9())
    with pytest.raises(ValueError, match="Ybus must"):
        make_voltage_product_maps(ybus, a)
