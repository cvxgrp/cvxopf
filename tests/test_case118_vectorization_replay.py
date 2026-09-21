"""Coordinate and sampling checks for independent historical-window replays."""

import random
import json

import numpy as np
import pytest

from experiments.case118_annual_hierarchy import streaming_runner as streaming
from experiments.case118_annual_hierarchy.p0_fixture import load_p0_fixture
from experiments.case118_vectorization_replay.sample import stratify
from experiments.case118_vectorization_replay.worker import pack_start, unpack_values


@pytest.mark.parametrize("perturb", [False, True])
def test_initial_coordinates_and_objective_survive_time_packing(perturb):
    fixture = load_p0_fixture(6)
    initial = {str(s.device_id): 500.0 for s in fixture.inputs.storage}
    target = {key: 400.0 for key in initial}
    storage = streaming._inner_storage(fixture.inputs, initial, target)
    step = streaming.build_window(fixture.inputs, "ac", 0, 3, storage)
    vector = streaming.build_window(
        fixture.inputs, "ac", 0, 3, storage, temporal_assembly="vectorized"
    )
    values = streaming.complete_flat_start(step)
    if perturb:
        rng = np.random.default_rng(827)
        for name, variable in streaming.variables_by_name(step).items():
            values[name] = variable.project(
                values[name] + rng.normal(0, 0.01, variable.shape)
            )
    streaming.assign_start(step, values)
    packed = pack_start(values, vector, list(initial.values()))
    streaming.assign_start(vector, packed)
    for name, value in unpack_values(packed, values).items():
        np.testing.assert_array_equal(value, values[name])
    np.testing.assert_array_equal(packed["soc"][:, 0], list(initial.values()))
    assert vector.prob.objective.value == pytest.approx(
        step.prob.objective.value, rel=1e-12
    )

    def auxiliary(build):
        solver = streaming.IPOPT()
        chain = streaming.SolvingChain(
            reductions=[
                streaming.CvxAttr2Constr(reduce_bounds=not solver.BOUNDED_VARIABLES),
                streaming.Dnlp2Smooth(),
                solver,
            ]
        )
        data, _ = chain.apply(build.prob)
        original = {v.id for v in build.prob.variables()}
        return np.sort(
            np.concatenate(
                [
                    np.asarray(v.value).ravel(order="F")
                    for v in data["problem"].variables()
                    if v.id not in original
                ]
            )
        )

    # Ordering changes, while generated auxiliary values must remain equivalent.
    np.testing.assert_array_equal(auxiliary(step), auxiliary(vector))


def test_stratified_sample_is_disjoint_and_weights_recover_population():
    population = [dict(iteration=i, seconds=float(i)) for i in range(1000)]
    chosen, strata = stratify(
        population, [(0.9, 90), (0.99, 20), (1, 7)], random.Random(61), "seconds"
    )
    assert len(chosen) == len({r["iteration"] for r in chosen}) == 117
    assert sum(r["population_weight"] for r in chosen) == pytest.approx(1000)
    assert [s["population"] for s in strata] == [900, 90, 10]
    assert sum(r["iteration"] >= 990 for r in chosen) == 7


def test_partial_analysis_waits_for_loser_reaping(tmp_path, monkeypatch):
    from experiments.case118_vectorization_replay import analyze

    monkeypatch.setattr(analyze, "OUT", tmp_path)
    (tmp_path / "sample.json").write_text(json.dumps({"selected": [{"iteration": 5}]}))
    root = tmp_path / "run"
    root.mkdir()
    (root / "completed.json").write_text(json.dumps({"iterations": []}))
    # Publication precedes primary cancellation and its lifecycle receipt.
    (root / "winner-000005.json").write_text("{}")
    with pytest.raises(ValueError, match="No completed replay windows"):
        analyze.analyze()


def test_weighted_quantile_uses_population_mass():
    from experiments.case118_vectorization_replay.analyze import weighted_quantile

    assert weighted_quantile([100, 20], [1, 9], [0.5, 0.9, 0.95]) == [20, 20, 100]


def test_angle_difference_ignores_full_turns():
    from experiments.case118_vectorization_replay.analyze import wrapped_angle_delta

    np.testing.assert_allclose(
        wrapped_angle_delta([359, 721, 1440], [1, 0, 0]), [-2, 1, 0]
    )


def test_temperature_snapshot_excludes_later_and_incomplete_samples(
    tmp_path, monkeypatch
):
    from datetime import datetime
    from experiments.case118_vectorization_replay import analyze

    monkeypatch.setattr(analyze, "OUT", tmp_path)
    folder = tmp_path / "temperature_telemetry" / "20260920T203816Z"
    folder.mkdir(parents=True)
    (folder / "metadata.json").write_text("{}")
    samples = [
        dict(
            received_utc=f"2026-09-20T20:39:{second:02d}+00:00",
            timestamp=f"2026-09-20T20:39:{second:02d}+00:00",
            temp={"cpu_temp_avg": temperature},
            pcpu_freq_mhz=4000,
        )
        for second, temperature in ((10, 54), (30, 60))
    ]
    (folder / "samples.jsonl").write_text(
        "".join(json.dumps(s) + "\n" for s in samples) + '{"partial":'
    )
    result = analyze.temperature_snapshot(
        datetime.fromisoformat("2026-09-20T20:39:20+00:00")
    )
    assert result["sample_count"] == 1
    assert result["cpu_temperature_c_range"] == [54, 54]
    assert (
        json.loads((tmp_path / "temperature_samples.jsonl").read_text()) == samples[0]
    )
