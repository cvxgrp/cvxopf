"""Non-solving plot arithmetic and artifact guard tests; no ignored-data dependency."""

import numpy as np
import pytest
from copy import deepcopy

from experiments.numerical_preparation import plot_tracy_three_step as p


def test_gross_flows_do_not_cancel_and_soc_has_initial_boundary():
    storage = [dict(initial_soc=10., capacity=20.), dict(initial_soc=30., capacity=60.)]
    result = dict(b=[[2., -2.], [-1., 1.], [-1., 1.]],
                  soc=[[8., 32.], [9., 31.], [10., 30.]])
    tr = p.trajectories(result, storage)
    np.testing.assert_array_equal(tr["charge"], [2., 1., 1.])
    np.testing.assert_array_equal(tr["discharge"], [2., 1., 1.])
    np.testing.assert_array_equal(tr["soc"][0], [10., 30.])
    np.testing.assert_array_equal(tr["soc_percent"][[0, -1]], [[50., 50.], [50., 50.]])
    np.testing.assert_array_equal(np.diff(tr["soc"], axis=0), -tr["b"])


def test_manifest_hash_failure_is_not_ignored(tmp_path):
    path = tmp_path / "example.json"
    path.write_text("{}")
    p.verify_hash(tmp_path, path.name, p.digest(path))
    with pytest.raises(ValueError, match="pinned artifact mismatch"):
        p.verify_hash(tmp_path, path.name, "0"*64)
    with pytest.raises(ValueError, match="pinned artifact mismatch"):
        p.verify_hash(tmp_path, "../example.json", "0"*64)


@pytest.mark.parametrize("root", [p.SOURCE, p.HISTORY])
def test_render_refuses_historical_output_before_writing(root):
    with pytest.raises(ValueError, match="immutable source"):
        p.render({}, {}, root / "must-not-create")


def synthetic_arrays():
    axes = dict(hours=[1165, 1166, 1167], boundary_hours=[1165, 1166, 1167, 1168],
                generators=[dict(bus=1)], storage=[dict(bus=2, device_id="battery")],
                nondispatchable=[dict(bus=3, device_id="solar")], loads=[dict(bus=4)])
    inputs = deepcopy(axes)
    inputs["delta"] = 1
    inputs["storage"][0]["capacity"] = 20
    candidates = {}
    for label in p.METHODS:
        result = {key: [[0.], [0.], [0.]] for key in
                  ("Pg", "b", "soc", "p_nd", "p_load_served")}
        if label not in {"lossy_dc", "singlenode_dc"}:
            result.update({key: [[0.], [0.], [0.]] for key in ("Qg", "b_q", "q_nd")})
        candidates[label] = dict(result=result, common=dict(passed=True),
                                 coordinate_checks=dict(passed=True))
    return (dict(comparison=dict(axes=axes), candidates=candidates),
            dict(calls=[dict(mathematical_inputs=inputs)]))


def test_array_validation_preserves_dc_absence_and_device_identity():
    report, binding = synthetic_arrays()
    p.validate_arrays(report, binding)
    report["candidates"]["lossy_dc"]["result"]["b_q"] = [[0.], [0.], [0.]]
    with pytest.raises(ValueError, match="must be absent"):
        p.validate_arrays(report, binding)
    report, binding = synthetic_arrays()
    report["comparison"]["axes"]["storage"][0]["device_id"] = "other"
    with pytest.raises(ValueError, match="unaligned storage device"):
        p.validate_arrays(report, binding)


def test_array_validation_rejects_nonfinite_data_and_wrong_boundaries():
    report, binding = synthetic_arrays()
    report["candidates"]["ac_baseline"]["result"]["b"][1][0] = float("nan")
    with pytest.raises(ValueError, match="invalid array"):
        p.validate_arrays(report, binding)
    report, binding = synthetic_arrays()
    report["comparison"]["axes"]["boundary_hours"] = [1166, 1167, 1168]
    with pytest.raises(ValueError, match="unexpected time"):
        p.validate_arrays(report, binding)
