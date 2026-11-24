"""Assemble the collaborator archive from committed reader code and retained evidence.

Run from anywhere with Python 3.11+: python experiments/pes2027/handoff/build.py.
No study files are regenerated. Refuse to replace an existing delivery.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import zipfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
READER = "3b918192e931d1cd88cd65a13d0c8caceea463b6"
TRACY = "experiments/case118_tracy_2021"
RESULTS = TRACY + "/results/"
PGLIB = "experiments/case118_annual_hierarchy/source"
TOY = "experiments/case118_annual_hierarchy/s5_closeout/"
CSV = "experiments/battery_terminal/data/9q9wtp_gen_and_load.csv"
FIXTURE = "experiments/objective_assembly_qualification/results/qualification_002/binding.json"
TRANSFER = "experiments/tracy_battery_transfer/results/transfer_001"
ROLLOUT = "experiments/tracy_ac_rollout/results/rollout_001"
SOURCE = [
    "LICENSE", "README.md", "pyproject.toml", "uv.lock", "src", "tests",
    "plans/socp-conditioning-evidence-inventory.json", PGLIB,
    *[TOY + f for f in ("aggregate_inputs.csv", "devices.json", "input_summary.json", "provenance.json")],
    ":(glob)experiments/**/*.py",
    *["experiments/" + d for d in (
        "pes2027", "case118_tracy_2021", "tracy_battery_transfer", "tracy_ac_rollout",
        "tracy_socp_transfer_preflight", "numerical_preparation", "objective_assembly_qualification",
    )],
]
RETAINED = [
    CSV, FIXTURE, TRANSFER, ROLLOUT,
    "experiments/numerical_preparation/results/matched_comparison_001",
    *[RESULTS + d for d in (
        "stage_a_active_inputs", "stage_c", "e3/binding.json", "e3_prepared", "e3_plus",
        "e3_plus_assessment", "pes_figures_review_001", "pes_soc_review_003",
        "pes_figures_names_001", "pes_soc_names_001", "pes_network_review_002",
        "pes_rollout_soc_names_001", "pes_joint_ac_controls_001",
    )],
]
PINS = {
    CSV: "45e11f061d736741b18334aea0e9525c355c1a13068c291c1db6ed2e614b1b6f",
    PGLIB + "/pglib_opf_case118_ieee.m": "b1af0833849040c04babc3700631cff0d9afa66b79c5d3e13ae79bdf516cec78",
    FIXTURE: "4d4ec1cb451207f4d0b0a17047258f902110c886bc73513ec3a1a152248b2d1f",
    TRANSFER + "/report.json": "3375d14c9d50a0a1a4d8439e3f1950313dfe373820a38ef667e35188383266a5",
    RESULTS + "pes_figures_review_001/figure_data.json": "0f07ff41289529b0d496f3085351b77f9c1817cf31512830fe3fffc6f5a27462",
    RESULTS + "pes_soc_review_003/soc_data.json": "9ba23d8dcc1137d9f7efa90962379ffc05545f146596b7c4975a982317b27c35",
}


def build(output):
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    files = {}
    with tempfile.TemporaryDirectory(dir=output.parent) as scratch:
        temp = Path(scratch)
        tar = temp / "source.tar"
        subprocess.run(["git", "archive", "-o", str(tar), READER, "--", *SOURCE], cwd=ROOT, check=True)
        target = temp / "delivery.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as z:
            def put(name, data):
                if name in files:
                    raise ValueError(f"duplicate delivery entry: {name}")
                info = zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_STORED if name.endswith((".gz", ".npz", ".png", ".pdf")) else zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                z.writestr(info, data, compresslevel=3)
                files[name] = hashlib.sha256(data).hexdigest()

            with tarfile.open(tar) as src:
                for member in src:
                    if member.isfile():
                        put("cvxopf/" + member.name, src.extractfile(member).read())
            for item in RETAINED:
                path = ROOT / item
                if not path.exists():
                    raise FileNotFoundError(path)
                for f in sorted(path.rglob("*")) if path.is_dir() else (path,):
                    if f.is_symlink():
                        raise ValueError(f"unexpected symlink: {f}")
                    if f.is_file() and not any(p.startswith(".") or p == "__pycache__" for p in f.relative_to(ROOT).parts):
                        put("cvxopf/" + f.relative_to(ROOT).as_posix(), f.read_bytes())
            for rel, digest in PINS.items():
                if files["cvxopf/" + rel] != digest:
                    raise ValueError(f"frozen source mismatch: {rel}")
            for f in ("README.md", "verify.py"):
                put(f, (HERE / f).read_bytes())
            manifest = {"reader_commit": READER, "retained_paths": RETAINED, "sha256": files.copy()}
            put("MANIFEST.json", (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
        # Same filesystem; fail if another build published the requested name.
        output.hardlink_to(target)
    print(f"{output}: {output.stat().st_size:,} bytes, {len(files):,} entries")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE.parent / "results/pes2027-case118-tracy.zip")
    build(parser.parse_args().output.resolve())
