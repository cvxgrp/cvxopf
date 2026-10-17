"""No-solve projection, boundary, provenance and immutable re-audit regressions."""

from copy import deepcopy

import clarabel
import numpy as np
import pytest

from experiments.convex_cost_qualification import audit as a, fixture as f, model as m, reaudit as re, run as r
from experiments.convex_cost_qualification.test_convex_cost_qualification import small_arm, synthetic_native
from experiments.numerical_preparation import run_qualification as q
from experiments.numerical_preparation.tracy_variables import check_coordinates


@pytest.fixture(autouse=True)
def forbid_optimizer(monkeypatch):
    monkeypatch.setattr(clarabel, "DefaultSolver", lambda *_a, **_k: pytest.fail("no optimizer authorized"))


def captured(name, excursion, *, scaled=True):
    _, kwargs, view, _, _ = f.construct(small_arm("lossy_dc", scaled))
    problem = m.canonical(view.solver)
    native = synthetic_native(view, problem)
    canonical_name = name+"_cost_coordinate" if name in view.scales else name
    item = next(v for v in problem.layout if v["name"] == canonical_name)
    variable = view.physical.variables[name]
    lower, upper = variable.get_bounds()
    value = (view.physical.data["storage_initial_soc"][0]+excursion if name == "soc"
             else np.asarray(lower).ravel(order="F")[0]-excursion)
    native["x"][item["start"]] = value*(view.scales[name].ravel(order="F")[0] if name in view.scales else 1.)
    full, result, _, _ = m.restore_view(view, problem, native)
    x, layout = np.asarray(full.x).copy(), deepcopy(problem.layout)
    for entry in layout:
        for original, scale in view.scales.items():
            if entry["name"] == original+"_cost_coordinate":
                x[entry["start"]:entry["stop"]] /= scale.ravel(order="F")
                entry["name"] = original
    coordinates = dict(full_x=x, layout=layout, raw={k: v.value.copy() for k, v in view.physical.variables.items()})
    return coordinates, result, kwargs, a.coordinate_policy(view)


@pytest.mark.parametrize("name,unit", [("Pg", "MW"), ("p_flows", "MW"), ("b", "MW"), ("p_nd", "MW"),
                                      ("soc", "MWh"), ("load_shed_fraction", "fraction")])
@pytest.mark.parametrize("scaled", [False, True])
def test_expected_box_projection_keeps_strict_mapping(name, unit, scaled):
    # Initial SoC is an equality, not a box projection in this fixture.
    coordinates, result, kwargs, policy = captured(name, 1e-9, scaled=scaled)
    checks = check_coordinates(coordinates, result, kwargs, **policy)
    assert checks["passed"] and checks["mapping_passed"] and checks["feasibility_passed"]
    if name != "soc":
        assert checks["bound_excursions"][name]["unit"] == unit
        assert checks["bound_excursions"][name]["warning"]
        assert checks["canonical_to_restored"][name]["maximum"] <= 1e-12
        assert not check_coordinates(coordinates, result, kwargs)["passed"]
    else:
        assert checks["boundary_feasibility"]["initial_soc"]["maximum"] == pytest.approx(1e-9)


@pytest.mark.parametrize("name,excursion", [("Pg", 3e-7), ("p_flows", 3e-7), ("b", 3e-5),
                                          ("p_nd", 3e-5), ("load_shed_fraction", 2e-8)])
def test_material_raw_box_excursion_remains_hard(name, excursion):
    coordinates, result, kwargs, policy = captured(name, excursion)
    checks = check_coordinates(coordinates, result, kwargs, **policy)
    assert checks["mapping_passed"] and not checks["passed"]
    assert not checks["bound_excursions"][name]["passed"]


@pytest.mark.parametrize("excursion,passed", [(1e-8, True), (2e-4, False)])
def test_initial_boundary_is_separate_physical_feasibility(excursion, passed):
    coordinates, result, kwargs, policy = captured("soc", excursion)
    checks = check_coordinates(coordinates, result, kwargs, **policy)
    assert checks["mapping_passed"] and checks["passed"] == passed
    assert "initial_soc" not in checks["canonical_to_public"]
    assert checks["boundary_feasibility"]["initial_soc"]["unit"] == "MWh"
    assert not check_coordinates(coordinates, result, kwargs)["passed"]


@pytest.mark.parametrize("corruption", ["unit", "transpose", "nonfinite", "missing", "raw"])
def test_unexplained_mapping_errors_remain_hard(corruption):
    coordinates, result, kwargs, policy = captured("Pg", 1e-9)
    if corruption == "unit":
        result["Pg"] = np.asarray(result["Pg"])/kwargs["case"]["baseMVA"]
    elif corruption == "transpose":
        result["Pg"] = np.asarray(result["Pg"]).T
    elif corruption == "nonfinite":
        result["Pg"] = np.full_like(result["Pg"], np.nan)
    elif corruption == "missing":
        del result["Pg"]
    else:
        coordinates["raw"]["Pg"][0, 0] += 1e-6
    checks = check_coordinates(coordinates, result, kwargs, **policy)
    assert not checks["mapping_passed"] and not checks["passed"]


def test_only_declared_bounds_are_projected():
    coordinates, result, kwargs, policy = captured("Pg", 1e-9)
    # Removing the declared box may not be compensated by a loose mapping gate.
    policy["leaf_boxes"].pop("Pg")
    assert not check_coordinates(coordinates, result, kwargs, **policy)["passed"]
    entry = next(v for v in coordinates["layout"] if v["name"] == "Pg")
    entry["stop"] += 1
    with pytest.raises(ValueError, match="layout"):
        check_coordinates(coordinates, result, kwargs, **policy)


def contexts():
    before = dict(commit="29f8aea803386149f49854b5fb22b71615f664b9", clean=True, installed={"adapter": "original"},
        sources={re.PREFIX+"audit.py": "old", re.PREFIX+"model.py": "same"}, convex_cost_qualification_sources={})
    return before, deepcopy(before)


def test_context_allows_only_checker_reader_transition():
    before, now = contexts()
    now["clean"] = False
    now["sources"][re.PREFIX+"audit.py"] = "corrected"
    now["sources"][re.PREFIX+"reaudit.py"] = "new"
    re.verify_context(before, now)
    now["sources"][re.PREFIX+"model.py"] = "different physics"
    with pytest.raises(ValueError, match="unapproved source"):
        re.verify_context(before, now)


@pytest.mark.parametrize("change", ["environment", "removed", "added"])
def test_context_rejects_unapproved_changes(change):
    before, now = contexts()
    if change == "environment":
        now["installed"]["adapter"] = "changed"
    elif change == "removed":
        now["sources"].pop(re.PREFIX+"model.py")
    else:
        now["sources"]["src/cvxopf/new.py"] = "unapproved"
    with pytest.raises(ValueError):
        re.verify_context(before, now)


def test_non_coordinate_results_must_be_identical():
    before = dict(passed=False, coordinate_checks={"passed": False}, result={"Pg": [[1.]]}, common={"passed": True})
    after = before | dict(passed=True, coordinate_checks={"passed": True})
    re.unchanged_checks(before, after)
    with pytest.raises(ValueError, match="published"):
        re.unchanged_checks(before, after | dict(result={"Pg": [[2.]]}))


def test_immutable_separate_output_and_pinned_input(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError, match="separate"):
        re.reaudit(source, source / "revised")
    output = tmp_path / "output"
    output.mkdir()
    with pytest.raises(FileExistsError):
        re.reaudit(source, output)
    q.atomic_json(source / "binding.json", {})
    with pytest.raises(ValueError, match="pinned"):
        re.verify_original(source)


def test_real_archive_replays_old_then_corrected_coordinates(monkeypatch, tmp_path):
    from experiments.convex_cost_qualification.test_convex_cost_qualification import archived_worker
    folder = archived_worker(monkeypatch, tmp_path)
    # Retain a genuine native archive/inverse chain and replace only the saved
    # audit with the historical policy, as the original run did.
    binding = r.verify_binding(tmp_path)
    row = binding["rows"][0]
    record = q.read(folder / "result.json.gz")
    _, kwargs, view, stress, problem = r.checked_construction(row)
    old = a.assess(view, kwargs, stress, record["native"], None, problem.signature, historical_coordinates=True)
    record["checks"] = old
    (folder / "result.json.gz").unlink()  # Replace only this generated unit-test archive.
    q.atomic_gzip_json(folder / "result.json.gz", record)
    manifest = q.read(folder / "completion.json")
    manifest["artifacts"]["result.json.gz"] = q.digest(folder / "result.json.gz")
    q.atomic_json(folder / "completion.json", manifest)
    def historical(*args):
        return a.assess(*args, historical_coordinates=True)

    original = r.replay(tmp_path, binding, auditor=historical)
    q.atomic_json(tmp_path / "report.json", original)
    monkeypatch.setattr(re, "verify_original", lambda _: (binding, {}))
    progress, _ = re.evaluate(tmp_path)
    assert progress["accepted"] == 1 and progress["attempts"][0]["original_accepted"]
    q.atomic_json(folder / "completion.json", manifest | dict(artifacts={}))
    with pytest.raises(ValueError, match="manifest"):
        re.evaluate(tmp_path)
