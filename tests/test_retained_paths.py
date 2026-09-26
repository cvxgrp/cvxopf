"""Recorded identities survive relocation; mapping work is scoped to an operation."""

from collections import Counter
import hashlib
import json
from pathlib import Path

import pytest

from experiments import retained_paths
from experiments.case118_vectorization_replay import sample


@pytest.fixture
def relocation(tmp_path, monkeypatch):
    monkeypatch.setattr(retained_paths, "ROOT", tmp_path)
    old = Path("outputs/case118_vectorization_replay/sample.json")
    new = Path("experiments/case118_vectorization_replay/results/case118_vectorization_replay/sample.json")
    records = [dict(old=str(old), new=str(new))]
    for manifest, files in zip(retained_paths.MANIFESTS, (records, [])):
        path = tmp_path / manifest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(files=files)))
    destination = tmp_path / new
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b'{"frozen": true}\n')
    return tmp_path, old, destination


def test_recorded_identity_and_hash_rejection(relocation):
    root, old, new = relocation
    digest = hashlib.sha256(new.read_bytes()).hexdigest()
    with retained_paths.retained_operation():
        for saved in (old, root / old):
            assert retained_paths.retained_path(saved) == new
            assert sample.checked(dict(path=str(saved), sha256=digest)) == {"frozen": True}
        new.write_text(json.dumps({"frozen": False}))
        with pytest.raises(ValueError, match="Retained artifact changed"):
            sample.checked(dict(path=str(old), sha256=digest))
    assert not (root / "outputs").exists()


def test_exact_mapping_leaves_unmapped_and_external_paths_alone(relocation):
    root, old, new = relocation
    paths = (old.parent, old.parent / "missing.json",
             Path("outputs/case118_vectorization_replay_other/sample.json"),
             Path("outputs/s5_analysis/file.json"),
             Path("outputs/counterfactual-pilot-preparation-20260917/protocol.json"),
             root.parent / "external" / old, new)
    with retained_paths.retained_operation():
        for path in paths:
            assert retained_paths.retained_path(path) == path
    with pytest.raises(FileNotFoundError):
        sample.checked(dict(path=str(old.parent / "missing.json"), sha256="unused"))


def test_operation_loads_each_manifest_once_and_later_operations_reload(relocation, monkeypatch):
    root, old, new = relocation
    reads = Counter()
    original_read = Path.read_text
    def counted(path, *args, **kwargs):
        reads[path] += 1
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", counted)

    @retained_paths.retained_operation()
    def nested():
        return retained_paths.retained_path(old)

    @retained_paths.retained_operation()
    def operation():
        assert nested() == new
        assert retained_paths.retained_path(root / old) == new
        assert retained_paths.retained_path(old) == new
    operation()
    assert reads == Counter({root / p: 1 for p in retained_paths.MANIFESTS})
    replacement = new.with_name("replacement.json")
    (root / retained_paths.MANIFESTS[0]).write_text(json.dumps(dict(files=[
        dict(old=str(old), new=str(replacement.relative_to(root)))
    ])))
    assert nested() == replacement
    assert reads == Counter({root / p: 2 for p in retained_paths.MANIFESTS})


def test_failed_operation_releases_index(relocation):
    root, old, new = relocation
    with pytest.raises(RuntimeError):
        with retained_paths.retained_operation():
            assert retained_paths.retained_path(old) == new
            raise RuntimeError("interrupted analysis")
    (root / retained_paths.MANIFESTS[0]).write_text('{"files": []}')
    with retained_paths.retained_operation():
        assert retained_paths.retained_path(old) == old


def test_experiment_defaults_never_use_root_scratch():
    from experiments.case118_spacetime_pq_replay import run
    assert sample.OUT.is_relative_to(sample.ROOT / "experiments/case118_vectorization_replay/results")
    assert run.OUTPUT.is_relative_to(sample.ROOT / "experiments/case118_spacetime_pq_replay/results")
    assert run.PREVIOUS == sample.OUT
