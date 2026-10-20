"""Export exact public inputs for the offline Julia reference generator.

Run from the repository root with python -m scripts.socp_reference.export_inputs.
Pass an explicit raw directory; never edits reference fixtures.
"""

import argparse
import json
from pathlib import Path

from tests.socp_reference_cases import cases, input_hash, payload, policy, SETTINGS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, required=True, help="Directory for raw inputs, outside CI")
    destination = parser.parse_args().raw_dir
    destination.mkdir(parents=True, exist_ok=True)
    requests = {}
    for name, case in cases().items():
        lines = [f"function mpc = {name}", "mpc.version = '2';", f"mpc.baseMVA = {case['baseMVA']:.17g};"]
        for key in ("bus", "gen", "branch", "gencost"):
            lines.append(f"mpc.{key} = [")
            lines.extend(" ".join(format(float(x), ".17g") for x in row) + ";" for row in case[key])
            lines.append("];")
        (destination / f"{name}.m").write_text("\n".join(lines)+"\n")
        requests[name] = dict(case=payload(case), input_sha256=input_hash(case), policy=policy(name))
    (destination / "request.json").write_text(json.dumps(dict(settings=SETTINGS, cases=requests), indent=2)+"\n")


if __name__ == "__main__":
    main()
