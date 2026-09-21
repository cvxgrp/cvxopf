"""Collect machine-wide thermal observations during this replay only."""

from datetime import datetime, timezone
import json
import shutil
import subprocess
import threading
import time

from experiments.case118_annual_hierarchy.streaming_schema import atomic_immutable_json


class TemperatureCollector:
    def __init__(self, folder):
        self.folder = folder
        self.process = None
        self.thread = None
        self.first_sample = threading.Event()
        self.samples = 0
        self.errors = []

    def _collect(self):
        with (self.folder / "samples.jsonl").open("w") as stream:
            for line in self.process.stdout:
                try:
                    sample = json.loads(line)
                    sample["received_utc"] = datetime.now(timezone.utc).isoformat()
                    sample["received_monotonic_seconds"] = time.monotonic()
                    stream.write(json.dumps(sample) + "\n")
                    stream.flush()
                    self.samples += 1
                    self.first_sample.set()
                except (ValueError, TypeError) as error:
                    self.errors.append(str(error))

    def __enter__(self):
        self.folder.mkdir()
        binary = shutil.which("macmon")
        metadata = dict(
            started_utc=datetime.now(timezone.utc).isoformat(),
            scope="Machine-wide observational telemetry; external fan already on.",
            sample_interval_seconds=10,
        )
        if binary is None:
            metadata["unavailable_reason"] = "macmon is not installed"
        else:
            command = [binary, "pipe", "-i", "10000"]
            try:
                self.error_log = (self.folder / "stderr.log").open("w")
                self.process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=self.error_log, text=True,
                )
                self.thread = threading.Thread(target=self._collect, daemon=True)
                self.thread.start()
                self.first_sample.wait(timeout=15)
                metadata.update(command=command, pid=self.process.pid,
                                sample_received_before_solves=self.first_sample.is_set())
                if not self.first_sample.is_set():
                    metadata["unavailable_reason"] = "No telemetry received before solve admission"
            except OSError as error:
                metadata["unavailable_reason"] = str(error)
        atomic_immutable_json(self.folder / "metadata.json", metadata)
        return self

    def __exit__(self, *exc):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            self.thread.join(timeout=5)
            self.process.stdout.close()
        if hasattr(self, "error_log"):
            self.error_log.close()
        atomic_immutable_json(self.folder / "finished.json", dict(
            finished_utc=datetime.now(timezone.utc).isoformat(),
            samples=self.samples, errors=self.errors,
        ))
