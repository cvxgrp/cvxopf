"""Time alignment and energy accounting in the saved-solution explorer."""
import numpy as np
import pytest

from experiments.m14_time_vectorization.analyze_tracy_week import difference_table, series


@pytest.fixture
def bundle():
    trajectory = dict(b=[[2.], [-2.]], soc=[[498.], [500.]], Pg=[[10.,20.,30.],[11.,20.,30.]],
                      p_nd=[[3.,4.],[1.,2.]])
    other = dict(trajectory, b=[[1.],[-1.]], soc=[[499.],[500.]])
    return dict(inputs=dict(storage_initial_soc_mwh=500.,load_mw=[69.,62.],
                            renewable_availability_mw=[[5.,6.],[7.,8.]],
                            renewable_ids=['dist_solar_bus_5','wind_bus_2'],generator_ids=['G1','G2','G3']),
                solutions={'lossy_dc_stepwise':dict(trajectory=trajectory),
                           'lossy_dc_vectorized':dict(trajectory=other)})


def test_soc_uses_boundary_times_and_keeps_fixed_endpoints(bundle):
    x, y, units = series(bundle,'lossy_dc_stepwise','State of charge')
    np.testing.assert_array_equal(x,[0,1,2])
    np.testing.assert_array_equal(y,[500.,498.,500.])
    assert units=='MWh'
    stats = difference_table(bundle,['lossy_dc_vectorized'],'lossy_dc_stepwise','State of charge','Total',(0,2))
    assert stats.iloc[0]['max_absolute_delta']==1
    assert stats.iloc[0]['mean_delta']==pytest.approx(1/3)
    assert stats.iloc[0]['energy_delta_mwh'] is None


def test_power_windows_exclude_end_interval_and_deltas_are_selected_minus_baseline(bundle):
    stats = difference_table(bundle,['lossy_dc_vectorized'],'lossy_dc_stepwise','Battery power','Total',(0,1))
    assert stats.iloc[0]['mean_delta']==-1
    assert stats.iloc[0]['energy_delta_mwh']==-1
    full = difference_table(bundle,['lossy_dc_vectorized'],'lossy_dc_stepwise','Battery power','Total',(0,2))
    assert full.iloc[0]['energy_delta_mwh']==0


def test_renewable_group_curtailment_and_supply_accounting(bundle):
    np.testing.assert_array_equal(series(bundle,'lossy_dc_stepwise','Curtailment','DG solar')[1],[2.,6.])
    np.testing.assert_array_equal(series(bundle,'lossy_dc_stepwise','Curtailment','Total')[1],[4.,12.])
    np.testing.assert_array_equal(series(bundle,'lossy_dc_stepwise','Supply minus load')[1],[0.,0.])
    np.testing.assert_array_equal(series(bundle,'lossy_dc_stepwise','Generation','G1')[1],[10.,11.])
