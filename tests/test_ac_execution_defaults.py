"""Default AC representation and scoped CVXPY compatibility policy."""

import cvxpy as cp
import numpy as np
import pandas as pd
import pytest

from cvxopf import OPFOptions, build_opf, build_opf_multistep
from cvxopf.testcases import case9


def multistep(**kwargs):
    case = case9()
    return build_opf_multistep(
        case, pd.DataFrame(np.tile(case['bus'][:, 2], (2, 1))),
        pd.DataFrame(np.tile(case['bus'][:, 3], (2, 1)))
        if kwargs.get('formulation', 'ac') == 'ac' else None,
        T=2, **kwargs
    )


def test_ac_default_is_combined_and_explicit_stepwise_remains_available():
    default = multistep()
    assert default.temporal_assembly == 'vectorized'
    assert default.variables['Pg'].shape[-1] == 2
    assert OPFOptions().vectorize_pq is True
    legacy = multistep(temporal_assembly='stepwise', options=OPFOptions(vectorize_pq=False))
    assert legacy.temporal_assembly == 'stepwise'
    assert isinstance(legacy.variables['Pg'], list)


@pytest.mark.parametrize('formulation', ['lossy_dc', 'singlenode_dc'])
def test_convex_time_default_is_vectorized(formulation):
    build = multistep(formulation=formulation)
    assert build.temporal_assembly == 'vectorized'
    assert build.automatic_sparse_dispatch is True


@pytest.mark.parametrize('automatic', [False, True])
def test_threshold_covers_build_and_solve_and_restores_on_error(monkeypatch, automatic):
    import cvxopf.problem as problem

    monkeypatch.setattr(cp.settings, 'SPARSE_DENSITY_THRESHOLD', 0.037)
    expected = 0.037 if automatic else 0.0
    original = problem._get_single_builders()['ac']

    def checked_builder(*args, **kwargs):
        assert cp.settings.SPARSE_DENSITY_THRESHOLD == expected
        return original(*args, **kwargs)

    monkeypatch.setattr(problem, '_get_single_builders', lambda: {'ac': checked_builder})
    build = build_opf(case9(), automatic_sparse_dispatch=automatic)
    assert cp.settings.SPARSE_DENSITY_THRESHOLD == 0.037

    def failed_solve(**kwargs):
        assert cp.settings.SPARSE_DENSITY_THRESHOLD == expected
        assert kwargs['nlp'] is True
        raise RuntimeError('sentinel')

    monkeypatch.setattr(build.prob, 'solve', failed_solve)
    with pytest.raises(RuntimeError, match='sentinel'):
        build.solve()
    assert cp.settings.SPARSE_DENSITY_THRESHOLD == 0.037

    def failed_builder(*args, **kwargs):
        assert cp.settings.SPARSE_DENSITY_THRESHOLD == expected
        raise RuntimeError('build sentinel')

    monkeypatch.setattr(problem, '_get_single_builders', lambda: {'ac': failed_builder})
    with pytest.raises(RuntimeError, match='build sentinel'):
        build_opf(case9(), automatic_sparse_dispatch=automatic)
    assert cp.settings.SPARSE_DENSITY_THRESHOLD == 0.037
