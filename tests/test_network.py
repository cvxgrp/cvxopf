"""
Tests for src/cvxopf/network.py
"""

import numpy as np
import pytest

from cvxopf.network import (
    reindex_case_to_consecutive,
    make_ybus_matpower,
    make_incidence_matrix,
    make_ybus_sparsity_mask,
)
from cvxopf.testcases import case9, case14


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reindexed(case_fn):
    """Return a reindexed copy of case_fn()."""
    c, _ = reindex_case_to_consecutive(case_fn())
    return c


# ---------------------------------------------------------------------------
# reindex_case_to_consecutive
# ---------------------------------------------------------------------------

class TestReindex:

    def test_bus_ids_are_zero_based_after_reindex(self, case9_raw):
        reindexed, _ = reindex_case_to_consecutive(case9_raw)
        expected = list(range(case9_raw["bus"].shape[0]))
        assert reindexed["bus"][:, 0].astype(int).tolist() == expected

    def test_mapping_is_none_when_already_zero_based(self, case9_raw):
        # Force the case to already be 0-based before calling reindex.
        c = case9_raw.copy()
        c["bus"]    = c["bus"].copy()
        c["branch"] = c["branch"].copy()
        c["gen"]    = c["gen"].copy()
        nb = c["bus"].shape[0]
        # Shift all IDs to 0-based
        old_ids  = c["bus"][:, 0].astype(int)
        id_map   = {old: new for new, old in enumerate(old_ids)}
        c["bus"][:, 0]    = np.arange(nb)
        c["branch"][:, 0] = np.array([id_map[int(i)] for i in c["branch"][:, 0]])
        c["branch"][:, 1] = np.array([id_map[int(i)] for i in c["branch"][:, 1]])
        c["gen"][:, 0]    = np.array([id_map[int(i)] for i in c["gen"][:, 0]])
        _, mapping = reindex_case_to_consecutive(c)
        assert mapping is None

    def test_branch_f_bus_within_range(self, case9_raw):
        reindexed, _ = reindex_case_to_consecutive(case9_raw)
        nb      = reindexed["bus"].shape[0]
        f_buses = reindexed["branch"][:, 0].astype(int)
        assert f_buses.min() >= 0
        assert f_buses.max() < nb

    def test_branch_t_bus_within_range(self, case9_raw):
        reindexed, _ = reindex_case_to_consecutive(case9_raw)
        nb      = reindexed["bus"].shape[0]
        t_buses = reindexed["branch"][:, 1].astype(int)
        assert t_buses.min() >= 0
        assert t_buses.max() < nb

    def test_gen_bus_within_range(self, case9_raw):
        reindexed, _ = reindex_case_to_consecutive(case9_raw)
        nb       = reindexed["bus"].shape[0]
        gen_buses = reindexed["gen"][:, 0].astype(int)
        assert gen_buses.min() >= 0
        assert gen_buses.max() < nb

    def test_ext_to_int_mapping_covers_all_original_ids(self, case9_raw):
        _, mapping = reindex_case_to_consecutive(case9_raw)
        original_ids = set(case9_raw["bus"][:, 0].astype(int).tolist())
        assert set(mapping.keys()) == original_ids

    def test_ext_to_int_values_are_consecutive(self, case9_raw):
        nb = case9_raw["bus"].shape[0]
        _, mapping = reindex_case_to_consecutive(case9_raw)
        assert sorted(mapping.values()) == list(range(nb))

    def test_duplicate_bus_ids_raise(self, case9_raw):
        c = case9_raw.copy()
        c["bus"] = c["bus"].copy()
        c["bus"][1, 0] = c["bus"][0, 0]   # duplicate BUS_I
        with pytest.raises(ValueError, match="Duplicate"):
            reindex_case_to_consecutive(c)

    def test_unknown_f_bus_raises(self, case9_raw):
        c = case9_raw.copy()
        c["branch"] = c["branch"].copy()
        c["branch"][0, 0] = 9999
        with pytest.raises(ValueError, match="unknown bus"):
            reindex_case_to_consecutive(c)

    def test_unknown_t_bus_raises(self, case9_raw):
        c = case9_raw.copy()
        c["branch"] = c["branch"].copy()
        c["branch"][0, 1] = 9999
        with pytest.raises(ValueError, match="unknown bus"):
            reindex_case_to_consecutive(c)

    def test_unknown_gen_bus_raises(self, case9_raw):
        c = case9_raw.copy()
        c["gen"] = c["gen"].copy()
        c["gen"][0, 0] = 9999
        with pytest.raises(ValueError, match="unknown bus"):
            reindex_case_to_consecutive(c)

    @pytest.mark.parametrize("case_fn", [case9, case14])
    def test_reindex_is_idempotent(self, case_fn):
        """Reindexing an already-reindexed case should be a no-op."""
        c1, _  = reindex_case_to_consecutive(case_fn())
        c2, m2 = reindex_case_to_consecutive(c1)
        assert m2 is None
        np.testing.assert_array_equal(c1["bus"],    c2["bus"])
        np.testing.assert_array_equal(c1["branch"], c2["branch"])
        np.testing.assert_array_equal(c1["gen"],    c2["gen"])


# ---------------------------------------------------------------------------
# make_ybus_matpower
# ---------------------------------------------------------------------------

class TestMakeYbus:

    @pytest.mark.parametrize("case_fn,nb", [(case9, 9), (case14, 14)])
    def test_shape(self, case_fn, nb):
        Y = make_ybus_matpower(_reindexed(case_fn))
        assert Y.shape == (nb, nb)

    @pytest.mark.parametrize("case_fn", [case9, case14])
    def test_dtype_is_complex(self, case_fn):
        Y = make_ybus_matpower(_reindexed(case_fn))
        assert Y.dtype == np.complex128

    @pytest.mark.parametrize("case_fn", [case9, case14])
    def test_symmetric(self, case_fn):
        Y = make_ybus_matpower(_reindexed(case_fn))
        np.testing.assert_allclose(Y, Y.T, atol=1e-10,
                                   err_msg="Ybus should be symmetric")

    @pytest.mark.parametrize("case_fn", [case9, case14])
    def test_diagonal_nonzero(self, case_fn):
        Y = make_ybus_matpower(_reindexed(case_fn))
        assert np.all(np.abs(np.diag(Y)) > 0), \
            "All diagonal entries of Ybus should be nonzero"

    @pytest.mark.parametrize("case_fn", [case9, case14])
    def test_row_sums_small(self, case_fn):
        """
        For a network with no shunts, row sums of Ybus should be near zero
        (Kirchhoff current law). With shunts present they will not be exactly
        zero, but the imaginary parts should dominate. We test that the real
        parts of the row sums are small relative to the diagonal.
        """
        c = _reindexed(case_fn)
        Y = make_ybus_matpower(c)
        # Bus shunt conductances (Gs) shift real row sums; allow for that.
        gs  = c["bus"][:, 4].astype(float) / float(c["baseMVA"])
        row_sums_real = np.abs(np.real(Y.sum(axis=1)) - gs)
        diag_real     = np.abs(np.real(np.diag(Y)))
        assert np.all(row_sums_real < 1e-8 * diag_real + 1e-10)

    def test_zero_impedance_branch_raises(self, case9_raw):
        c = case9_raw.copy()
        c["branch"] = c["branch"].copy()
        c["branch"][0, 2] = 0.0   # BR_R = 0
        c["branch"][0, 3] = 0.0   # BR_X = 0  =>  z = 0
        c, _ = reindex_case_to_consecutive(c)
        with pytest.raises(ValueError, match="r = x = 0"):
            make_ybus_matpower(c)

    def test_out_of_service_branch_excluded(self, case9_raw):
        """Setting BR_STATUS=0 on a branch should change the Ybus."""
        c_on  = case9_raw.copy()
        c_off = case9_raw.copy()
        c_off["branch"]    = c_off["branch"].copy()
        c_off["branch"][0, 10] = 0   # BR_STATUS = 0

        c_on,  _ = reindex_case_to_consecutive(c_on)
        c_off, _ = reindex_case_to_consecutive(c_off)

        Y_on  = make_ybus_matpower(c_on)
        Y_off = make_ybus_matpower(c_off)
        assert not np.allclose(Y_on, Y_off), \
            "Disabling a branch should change the Ybus"


# ---------------------------------------------------------------------------
# make_incidence_matrix
# ---------------------------------------------------------------------------

class TestIncidenceMatrix:

    @pytest.mark.parametrize("case_fn,nb,ng", [(case9, 9, 3), (case14, 14, 5)])
    def test_shape(self, case_fn, nb, ng):
        Cg = make_incidence_matrix(_reindexed(case_fn))
        assert Cg.shape == (nb, ng)

    @pytest.mark.parametrize("case_fn", [case9, case14])
    def test_entries_are_zero_or_one(self, case_fn):
        Cg = make_incidence_matrix(_reindexed(case_fn))
        assert np.all((Cg == 0) | (Cg == 1))

    @pytest.mark.parametrize("case_fn", [case9, case14])
    def test_each_in_service_gen_has_exactly_one_entry(self, case_fn):
        c      = _reindexed(case_fn)
        Cg     = make_incidence_matrix(c)
        status = c["gen"][:, 7].astype(int)
        for k in range(Cg.shape[1]):
            col_sum = int(Cg[:, k].sum())
            if status[k] == 1:
                assert col_sum == 1, \
                    f"In-service generator {k} should appear exactly once in Cg"
            else:
                assert col_sum == 0, \
                    f"Out-of-service generator {k} should not appear in Cg"

    @pytest.mark.parametrize("case_fn", [case9, case14])
    def test_gen_placed_on_correct_bus(self, case_fn):
        c       = _reindexed(case_fn)
        Cg      = make_incidence_matrix(c)
        gen_bus = c["gen"][:, 0].astype(int)
        status  = c["gen"][:, 7].astype(int)
        for k in range(Cg.shape[1]):
            if status[k] == 1:
                assert Cg[gen_bus[k], k] == 1.0, \
                    f"Generator {k} should be placed on bus {gen_bus[k]}"


# ---------------------------------------------------------------------------
# make_ybus_sparsity_mask
# ---------------------------------------------------------------------------

class TestSparsityMask:

    @pytest.mark.parametrize("case_fn", [case9, case14])
    def test_E_and_Z_partition_all_entries(self, case_fn):
        c    = _reindexed(case_fn)
        Y    = make_ybus_matpower(c)
        nb   = c["bus"].shape[0]
        E, Z = make_ybus_sparsity_mask(Y)
        total = len(E[0]) + len(Z[0])
        assert total == nb * nb

    @pytest.mark.parametrize("case_fn", [case9, case14])
    def test_E_and_Z_are_disjoint(self, case_fn):
        c    = _reindexed(case_fn)
        Y    = make_ybus_matpower(c)
        E, Z = make_ybus_sparsity_mask(Y)
        e_set = set(zip(E[0].tolist(), E[1].tolist()))
        z_set = set(zip(Z[0].tolist(), Z[1].tolist()))
        assert e_set.isdisjoint(z_set)

    @pytest.mark.parametrize("case_fn", [case9, case14])
    def test_diagonal_entries_in_E(self, case_fn):
        """Diagonal of Ybus is always nonzero so must be in E."""
        c    = _reindexed(case_fn)
        Y    = make_ybus_matpower(c)
        nb   = c["bus"].shape[0]
        E, _ = make_ybus_sparsity_mask(Y)
        e_set = set(zip(E[0].tolist(), E[1].tolist()))
        for i in range(nb):
            assert (i, i) in e_set, \
                f"Diagonal entry ({i},{i}) should be in E"

    def test_tol_zero_gives_exact_sparsity(self):
        """With tol=0, only exactly-zero entries go to Z."""
        Y    = np.array([[1+2j, 0+0j], [0+0j, 3+4j]], dtype=complex)
        E, Z = make_ybus_sparsity_mask(Y, tol=0.0)
        e_set = set(zip(E[0].tolist(), E[1].tolist()))
        z_set = set(zip(Z[0].tolist(), Z[1].tolist()))
        assert e_set == {(0, 0), (1, 1)}
        assert z_set == {(0, 1), (1, 0)}

    def test_tol_excludes_small_entries(self):
        """Entries below tol should be moved to Z."""
        Y    = np.array([[1+0j, 1e-13+0j], [1e-13+0j, 1+0j]], dtype=complex)
        E, Z = make_ybus_sparsity_mask(Y, tol=1e-12)
        e_set = set(zip(E[0].tolist(), E[1].tolist()))
        z_set = set(zip(Z[0].tolist(), Z[1].tolist()))
        assert e_set == {(0, 0), (1, 1)}
        assert z_set == {(0, 1), (1, 0)}
