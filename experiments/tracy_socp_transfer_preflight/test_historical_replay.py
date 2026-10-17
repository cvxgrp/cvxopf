"""Post-commit read-only replay must not confer authority to execute solves."""

import ast
from copy import deepcopy
import json
from pathlib import Path

import pytest

from experiments.tracy_battery_transfer_slice import run as sliced
from experiments.tracy_battery_transfer_feasibility import run as feasibility
from experiments.tracy_socp_transfer_preflight import historical_replay as h
from experiments.tracy_socp_transfer_preflight import preflight as p


@pytest.fixture(autouse=True)
def forbid_optimization(monkeypatch):
    for key in sliced.THREAD_KEYS:
        monkeypatch.setenv(key, "1")
    with p.no_optimization():
        yield


def committed_context(module):
    value = deepcopy(module.context())
    value.update(commit="simulated-post-commit", clean=True)
    # Simulate git ls-files acquiring these previously untracked packages,
    # without staging, committing, changing HEAD, or writing any artifact.
    for prefix in h.ANALYSIS_DIRECTORIES:
        for path in (h.ROOT / prefix).rglob("*.py"):
            if "results" not in path.relative_to(h.ROOT).parts:
                value["sources"][str(path.relative_to(h.ROOT))] = h.digest(path)
    return value


@pytest.mark.parametrize("module", [sliced, feasibility])
def test_post_commit_read_only_validation_accepts_but_execution_rejects(
    monkeypatch, module
):
    current = committed_context(module)
    monkeypatch.setattr(module, "context", lambda: deepcopy(current))
    if module is sliced:
        root = sliced.m.HERE / "results/slice_002"
        binding = sliced.verify(root)
        assert binding["context"]["commit"] != current["commit"]
        with pytest.raises(ValueError, match="bound source/environment"):
            sliced.verify(root, for_execution=True)
    else:
        binding = module.q.read(module.OUTPUT / "binding.json")
        module.validate_context(binding, for_execution=False)
        with pytest.raises(ValueError, match="bound diagnostic source/environment"):
            module.validate_context(binding, for_execution=True)


@pytest.mark.parametrize("module", [sliced, feasibility])
def test_full_status_after_simulated_commit_preserves_outcomes(monkeypatch, module):
    current = committed_context(module)
    monkeypatch.setattr(module, "context", lambda: deepcopy(current))
    root = sliced.m.HERE / "results/slice_002" if module is sliced else module.OUTPUT
    before = {str(path): h.digest(path) for path in root.rglob("*") if path.is_file()}
    result = module.status(root)
    assert result["launches"] == result["finalized"]
    if module is sliced:
        assert result["accepted"] == 1 and result["finalized"] == 4
        assert [a["classification"] for a in result["attempts"]] == [
            "accepted",
            "native_rejection",
            "native_rejection",
            "native_rejection",
        ]
    else:
        assert result["feasible_cases"] == 0 and result["finalized"] == 6
    assert all(a["resources_verified"] for a in result["attempts"])
    assert before == {
        str(path): h.digest(path) for path in root.rglob("*") if path.is_file()
    }


def context_fixture():
    return dict(
        commit="old",
        clean=False,
        sources={"src/cvxopf/ac_problem.py": "math"},
        slice_sources={"experiments/tracy_battery_transfer_slice/model.py": "model"},
        packages={"numpy": "fixed"},
        installed={"library": "fixed"},
        thread_environment={"OMP_NUM_THREADS": "1"},
        protocol_sha256="protocol",
    )


@pytest.mark.parametrize(
    "change",
    [
        "math",
        "missing_math",
        "model",
        "package",
        "library",
        "threads",
        "protocol",
        "outside_addition",
    ],
)
def test_historical_comparison_keeps_source_and_environment_gates(change):
    saved = context_fixture()
    current = deepcopy(saved)
    current.update(commit="new", clean=True)
    if change == "math":
        current["sources"]["src/cvxopf/ac_problem.py"] = "changed"
    elif change == "missing_math":
        current["sources"].clear()
    elif change == "model":
        current["slice_sources"][
            "experiments/tracy_battery_transfer_slice/model.py"
        ] = "changed"
    elif change == "package":
        current["packages"]["numpy"] = "changed"
    elif change == "library":
        current["installed"]["library"] = "changed"
    elif change == "threads":
        current["thread_environment"]["OMP_NUM_THREADS"] = "2"
    elif change == "protocol":
        current["protocol_sha256"] = "changed"
    else:
        current["sources"]["src/cvxopf/new_physics.py"] = "new"
    assert not h.historical_context_matches(saved, current)


def test_reader_transition_is_exact_not_a_source_ignore_list():
    receipt = json.loads(h.SOURCE_CHANGES.read_text())
    for path, row in receipt["changes"].items():
        assert h.digest(h.ROOT / row["snapshot_path"]) == row["before_sha256"]
        assert h.digest(h.ROOT / path) == row["after_sha256"]
        assert h.source_matches(path, row["before_sha256"])
        assert not h.source_matches(
            path, "another historical version", row["after_sha256"]
        )
        assert not h.source_matches(
            path, row["before_sha256"], "an unreviewed future edit"
        )


def test_source_checkpoint_only_changes_reader_control_flow():
    receipt = json.loads(h.SOURCE_CHANGES.read_text())["changes"]
    allowed = {
        "tracy_battery_transfer_slice": {"verify", "worker", "run"},
        "tracy_battery_transfer_feasibility": {
            "validate_context",
            "worker",
            "status",
            "run",
        },
        "tracy_socp_transfer_preflight": {"historical_context_matches"},
    }
    for path, row in receipt.items():
        if Path(path).name.startswith("test_"):
            continue
        before = ast.parse((h.ROOT / row["snapshot_path"]).read_text())
        after = ast.parse((h.ROOT / path).read_text())
        omitted = allowed[Path(path).parent.name]
        for tree in (before, after):
            tree.body = [
                node
                for node in tree.body
                if not isinstance(node, ast.FunctionDef) or node.name not in omitted
            ]
        assert ast.dump(before) == ast.dump(after)


@pytest.mark.parametrize("module", [sliced, feasibility])
def test_changed_historical_inventory_is_rejected_even_after_commit(
    monkeypatch, module
):
    current = committed_context(module)
    monkeypatch.setattr(module, "context", lambda: deepcopy(current))
    if module is sliced:
        monkeypatch.setattr(module, "historical_hashes", lambda: {})
        with pytest.raises(ValueError, match="historical evidence"):
            module.verify(module.m.HERE / "results/slice_002")
    else:
        monkeypatch.setattr(module, "historical_files", lambda: {})
        binding = module.q.read(module.OUTPUT / "binding.json")
        with pytest.raises(ValueError, match="historical transfer/recovery artifacts"):
            module.validate_context(binding, for_execution=False)


def test_loop_reproduction_uses_read_only_verifier_without_changing_its_source():
    # The existing analyze.main calls sliced.verify(root), whose default is
    # now read-only. No change to the hash-bound interval implementation.
    path = Path(p.loop.__file__)
    evidence = json.loads(p.LOOP_EVIDENCE.read_text())
    assert h.digest(path) == evidence["analysis_sha256"]
    main = next(
        n
        for n in ast.parse(path.read_text()).body
        if isinstance(n, ast.FunctionDef) and n.name == "main"
    )
    calls = [
        n
        for n in ast.walk(main)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "run"
        and n.func.attr == "verify"
    ]
    assert len(calls) == 1 and not calls[0].keywords


@pytest.mark.parametrize(
    "module,method", [(sliced, "verify"), (feasibility, "validate_context")]
)
def test_launch_loop_retains_execution_gate(module, method):
    tree = ast.parse(Path(module.__file__).read_text())
    run = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run"
    )
    loop = next(n for n in ast.walk(run) if isinstance(n, ast.While))
    gate = loop.body[0].value
    assert isinstance(gate, ast.Call) and gate.func.id == method
    assert (
        gate.keywords[0].arg == "for_execution" and gate.keywords[0].value.value is True
    )
