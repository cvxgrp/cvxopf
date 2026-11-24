"""Check the extracted delivery using stdlib; --inputs also rebuilds the Tracy mapping."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


HERE = Path(__file__).resolve().parent
REPO = HERE / "cvxopf"


def verify_files():
    manifest = json.loads((HERE / "MANIFEST.json").read_text())
    for name, expected in manifest["sha256"].items():
        path = HERE / name
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise ValueError(f"SHA-256 mismatch: {name}")
    print(f"Verified {len(manifest['sha256']):,} files; reader {manifest['reader_commit']}.", flush=True)
    return manifest


def install_evidence(destination, manifest):
    destination = destination.resolve()
    actual_root = subprocess.check_output(
        ["git", "-C", str(destination), "rev-parse", "--show-toplevel"], text=True
    ).strip()
    if destination != Path(actual_root).resolve() or destination == REPO.resolve():
        raise ValueError("Choose the root of a separate Git clone.")
    sha = subprocess.check_output(
        ["git", "-C", str(destination), "rev-parse", "HEAD"], text=True
    ).strip()
    historical = {
        "2edc6d3bbeac85bd78f5bf6d9cdc387f851eb0f0",
        "13ad35bc5f0e9f8b5d20d54a55d2ab2cd7a26df8",
        "4fbd85f3bd80a573edac4da284e325804f1d77a3",
        "8267dbf857872e4b6adcbf6c84dc05f002d63f60",
        "b260e354d6eb63dfa559af7b13996617e9cdcd7a",
    }
    if sha not in historical | {manifest["reader_commit"]}:
        raise ValueError("Choose the pinned reader or a documented execution revision.")
    if subprocess.check_output(["git", "-C", str(destination), "status", "--porcelain"]):
        raise ValueError("Install into a clean checkout.")
    # Earlier revisions do not yet contain the later experiments' .gitignore.
    # Never introduce those paths: doing so invalidates the clean-source gate.
    paths = manifest["retained_paths"]
    ignored = subprocess.run(
        ["git", "-C", str(destination), "check-ignore", "--stdin"],
        input="".join(s + "\n" for s in paths), text=True, capture_output=True, check=False,
    )
    if ignored.returncode not in (0, 1):
        raise RuntimeError(ignored.stderr)
    roots = [Path(s) for s in ignored.stdout.splitlines()]
    if "experiments/battery_terminal/data/9q9wtp_gen_and_load.csv" not in ignored.stdout.splitlines():
        raise ValueError("The destination does not ignore the source CSV.")
    copy = []
    for name, digest in manifest["sha256"].items():
        p = Path(name)
        if p.parts[0] != "cvxopf":
            continue
        rel = p.relative_to("cvxopf")
        if not any(rel == r or r in rel.parents for r in roots):
            continue
        target = destination / rel
        if target.resolve() != target or not target.is_relative_to(destination):
            raise ValueError(f"Unexpected destination: {target}")
        if target.exists():
            with target.open("rb") as f:
                if hashlib.file_digest(f, "sha256").hexdigest() != digest:
                    raise ValueError(f"Conflicting evidence at {target}; nothing copied.")
        else:
            copy.append((HERE / p, target))
    for source, target in copy:
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as i, target.open("xb") as o:
            shutil.copyfileobj(i, o)
    print(f"Installed {len(copy):,} missing evidence files ({len(roots)}/{len(paths)} "
          "retained paths apply at this revision); no existing file was overwritten.")


def verify_inputs():
    if not __debug__:
        raise RuntimeError("Run without -O so the independent scientific assertions are active.")
    sys.path[:0] = [str(REPO), str(REPO / "src")]
    import numpy as np
    from experiments.case118_annual_hierarchy.pglib_case import array_sha256
    from experiments.case118_tracy_2021 import prepare
    from experiments.case118_tracy_2021.audit import audit_inputs

    reference = json.loads((prepare.HERE / "stage_a/manifest.json").read_text())
    p = prepare.prepare()
    audit = audit_inputs(p)
    if not audit["passed"]:
        raise ValueError(audit)
    with np.load(prepare.HERE / "results/stage_a_active_inputs/active_inputs.npz") as raw:
        for key, record in reference["arrays"].items():
            a = getattr(p, key)
            if list(a.shape) != record["shape"] or array_sha256(a) != record["sha256"]:
                raise ValueError(f"Reconstruction differs from the Stage A record: {key}")
            np.testing.assert_array_equal(a, raw[key])
    for name in ("buses", "generators", "renewables", "batteries"):
        expected = reference["review_artifacts"][name + ".csv"]
        if hashlib.sha256(getattr(p, name).to_csv(index=False).encode()).hexdigest() != expected:
            raise ValueError(f"Realized device table differs: {name}")
    print("Rebuilt all 8,760 Tracy hours: three array hashes and four device tables match; no solves.")


def verify_results(replay):
    retained = REPO / "experiments/case118_tracy_2021/results"
    checks = [
        ("comparisons/figure_data.json", "pes_figures_names_001/figure_data.json",
         ("scenarios", "joint", "fixed", "rollouts")),
        ("soc/soc_data.json", "pes_soc_names_001/soc_data.json",
         ("storage_ids", "capacity_mwh", "cases")),
        ("rollout_soc/figure_data.json", "pes_rollout_soc_names_001/figure_data.json",
         ("case", "storage_ids", "capacity_mwh", "rollout_boundary_soc_mwh", "rollout_summary")),
        ("controls/distances.json", "pes_joint_ac_controls_001/distances.json", ("controls",)),
        ("network/manifest.json", "pes_network_review_002/manifest.json", ("layout_seed", "source_hashes")),
    ]
    for output, reference, fields in checks:
        actual = json.loads((replay / output).read_text())
        expected = json.loads((retained / reference).read_text())
        for field in fields:
            if actual[field] != expected[field]:
                raise ValueError(f"{output}: {field} differs; inspect before interpreting.")
        print(f"Matches {output}: {', '.join(fields)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--inputs", action="store_true")
    group.add_argument("--install-evidence", type=Path, metavar="CLEAN_READER_CLONE")
    group.add_argument("--results", type=Path, metavar="REPLAY_DIR")
    args = parser.parse_args()
    manifest = verify_files()
    if args.inputs:
        verify_inputs()
    if args.install_evidence:
        install_evidence(args.install_evidence, manifest)
    if args.results:
        verify_results(args.results)
