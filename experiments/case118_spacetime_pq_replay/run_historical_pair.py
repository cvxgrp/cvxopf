"""Run A once, and B only after acceptance, with process and thermal records."""

import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time

import prepare_historical as prep


def main():
    output = prep.BASE / "convergence-test"
    output.mkdir(exist_ok=False)
    binding = prep.BASE / "solve-binding.json"
    frozen = prep.read(binding)
    for path, expected in frozen["files"].items():
        if prep.digest(Path(path)) != expected:
            raise ValueError(f"Bound file changed: {path}")
    processes = subprocess.check_output(["ps", "-axo", "pid,etime,%cpu,command"], text=True)
    (output / "processes-before.txt").write_text(processes)
    # Inspect competing research workers; this supervisor is intentionally excluded.
    competing = [line for line in processes.splitlines() if any(
        marker in line for marker in (
            "solve_historical.py --arm", "diagnose_primary --", "speculative_worker",
            "case118_vectorization_replay.worker", "case118_spacetime_pq_replay.run",
        )
    )]
    if competing:
        raise ValueError(f"Potential competing solves: {competing}")
    (output / "authorization.json").write_text(json.dumps(dict(
        owner_authorized=True, external_fan_confirmed_on=True,
        binding_sha256=prep.digest(binding), pid=os.getpid(),
        started_unix=time.time(),
    ), indent=2) + "\n")
    rows = []
    with (output / "thermal.jsonl").open("w") as thermal, (output / "thermal-stderr.log").open("w") as errors:
        monitor = subprocess.Popen(
            ["macmon", "pipe", "-i", "10000"], stdout=subprocess.PIPE,
            stderr=errors, text=True,
        )
        selector = selectors.DefaultSelector()
        selector.register(monitor.stdout, selectors.EVENT_READ)

        def sample(timeout):
            if not selector.select(timeout):
                return False
            line = monitor.stdout.readline()
            if not line:
                raise RuntimeError("Thermal collector stopped")
            record = json.loads(line)
            if "cpu_temp_avg" not in record.get("temp", {}):
                raise ValueError("Thermal sample lacks temperature")
            thermal.write(line)
            thermal.flush()
            return True

        try:
            if not sample(15):
                raise RuntimeError("No thermal sample before launch")
            for arm in ("a", "b"):
                folder = output / arm
                command = [str(prep.BASE / f"env-{arm}/bin/python"), "-u",
                           str(Path(__file__).with_name("solve_historical.py")),
                           "--arm", arm, "--output", str(folder), "--binding", str(binding)]
                print(f"Starting arm {arm}", flush=True)
                with (output / f"{arm}-worker.log").open("w") as log, (output / f"{arm}-process.jsonl").open("w") as process_log:
                    started = time.monotonic()
                    worker = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, cwd=prep.ROOT)
                    (output / f"{arm}-launch.json").write_text(json.dumps(dict(
                        command=command, pid=worker.pid, started_unix=time.time(),
                    ), indent=2) + "\n")
                    while worker.poll() is None:
                        observation = subprocess.run(
                            ["ps", "-p", str(worker.pid), "-o", "pid=,etime=,%cpu=,rss="],
                            capture_output=True, text=True,
                        )
                        process_log.write(json.dumps(dict(unix=time.time(), output=observation.stdout,
                                                          returncode=observation.returncode)) + "\n")
                        process_log.flush()
                        sample(5)
                    row = dict(arm=arm, returncode=worker.returncode,
                               wall_seconds=time.monotonic() - started)
                if row["returncode"] != 0:
                    rows.append(row)
                    break
                row["summary"] = prep.read(folder / "summary.json")
                rows.append(row)
                print(json.dumps(row), flush=True)
                if not row["summary"]["accepted"]:
                    break
        finally:
            monitor.terminate()
            monitor.wait(timeout=10)
            selector.close()
            monitor.stdout.close()
            (output / "summary.json").write_text(json.dumps(rows, indent=2) + "\n")
    return 0 if rows and all(row["returncode"] == 0 for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
