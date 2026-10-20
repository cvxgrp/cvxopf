"""Select compact E2 evidence from hash-verified raw artifacts; never solves."""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def summarize(raw):
    raw_bytes = (raw / "run.json").read_bytes()
    root = json.loads(raw_bytes)
    if not root["accepted"] or root["solves_attempted"] != 8:
        raise ValueError("Expected the complete, accepted eight-solve E2 run")
    if [r["name"] for r in root["records"]] != ["case9", "case14", "mixed_vectorized", "mixed_stepwise"]:
        raise ValueError("Unexpected pair list")
    for item in root["records"]:
        data = (raw / item["artifact"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError(f"Changed raw record: {item['name']}")
        record = json.loads(data)
        if not record["accepted"] or record["summary"] != item["summary"]:
            raise ValueError(f"Raw/summary disagreement: {item['name']}")
        if not root["context"] == record["context"] == record["end_context"] == root["end_context"]:
            raise ValueError("Execution contexts differ")
    selected = deepcopy(root)
    selected.pop("end_context")  # equal to context, verified above
    selected.update(schema_version=1, raw_root_sha256=hashlib.sha256(raw_bytes).hexdigest(),
                    raw_directory=raw.name, scope="E2 matched AC/SOCP; not a certified bound or M11 closure")
    # Retain maxima/units/tolerances/worst locations in tracked evidence; all
    # per-interval diagnostic arrays and full primals remain in raw artifacts.
    def trim(value):
        if isinstance(value, dict):
            return {key: trim(child) for key, child in value.items() if key != "by_interval"}
        if isinstance(value, list):
            return [trim(child) for child in value]
        return value
    return trim(selected)


def main():
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=here / "results" / "e2")
    parser.add_argument("--output", type=Path, default=here / "E2_EVIDENCE.json")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    encoded = json.dumps(summarize(args.raw_dir), sort_keys=True, indent=2, allow_nan=False)+"\n"
    if args.check:
        if args.output.read_text() != encoded:
            raise ValueError("Selected evidence differs from retained raw artifacts")
        print("Selected E2 evidence matches retained raw artifacts")
    else:
        with args.output.open("x") as out:
            out.write(encoded)
        print(args.output)


if __name__ == "__main__":
    main()
