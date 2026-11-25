"""Preflight selection and input guards without retained data or solves."""

import json

import pytest

from experiments.ac_spatial_vectorization import preflight as check


def test_selects_the_six_published_ac_cases():
    selection = json.loads(check.SELECTION.read_text())
    cases = check.select_pes_arms(selection)
    assert list(cases) == [
        "large-surplus", "large-deficit", "deficit-depletion", "surplus-to-deficit",
        "deficit-to-surplus", "forced-shedding",
    ]
    assert {a["formulation"] for a in cases.values()} == {"ac"}
    assert {a["stop"] - a["start"] for a in cases.values()} == {24}
    ordinary = cases["large-deficit"]
    depletion = cases["deficit-depletion"]
    assert depletion["initial_soc_mwh"] != ordinary["initial_soc_mwh"]
    assert depletion["terminal_soc_mwh"] != ordinary["terminal_soc_mwh"]
    assert cases["forced-shedding"] == dict(ordinary, window="Forced shedding")


def test_rejects_a_different_complete_window():
    selection = json.loads(check.SELECTION.read_text())
    window = selection["periods"][0]["candidates"][0]
    window.update(start=window["start"] + 1, stop=window["stop"] + 1)
    with pytest.raises(ValueError, match="Unexpected or duplicate"):
        check.select_pes_arms(selection)


def test_rejects_missing_or_changed_case500_before_resolving_tracy(tmp_path):
    source = tmp_path / "pglib_opf_case500_goc.m"
    with pytest.raises(FileNotFoundError):
        next(check.preflight(source))
    source.write_text("function mpc = pglib_opf_case500_goc\n")
    with pytest.raises(ValueError, match="PGLib case SHA-256 mismatch"):
        next(check.preflight(source))


def test_workers_only_load_the_assigned_fixture(monkeypatch, tmp_path):
    def unexpected(*args, **kwargs):
        raise AssertionError("Loaded an unrelated benchmark fixture")

    with monkeypatch.context() as scoped:
        scoped.setattr(check, "tracy_inputs", unexpected)
        scoped.setattr(check, "load_case500", lambda path: {"source": path})
        assert check.fixture_inputs("pglib-500-goc", tmp_path)[2]["case"] == {"source": tmp_path}
    monkeypatch.setattr(check, "load_case500", unexpected)
    monkeypatch.setattr(check, "tracy_inputs", lambda names: iter(names))
    assert check.fixture_inputs("deficit-depletion", tmp_path) == "deficit-depletion"
