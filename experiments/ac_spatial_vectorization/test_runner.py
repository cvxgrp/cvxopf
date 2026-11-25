"""Fresh-process rehearsals for the real serial supervisor with fake workers."""

import fcntl
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread
import time

import pytest

from . import run


TEST_MODULE = "experiments.ac_spatial_vectorization.test_runner"
CLOCKS = dict(build_seconds=.1, preparation_seconds=.1, reduction_seconds=.2, derivative_seconds=.3)


def fake_worker(directory, mode, parent_fd):
    def watch():
        with os.fdopen(parent_fd, "rb") as pipe:
            pipe.read(1)
        os._exit(75)

    Thread(target=watch, daemon=True).start()
    request = run.read(directory / "request.json")
    run.atomic_json(directory / "phase.json", {"phase": "fake"})
    # Give the real supervisor time to observe the short fake child.
    time.sleep(.15)
    if mode == "sleep":
        time.sleep(20)
    if mode == "partial":
        run.atomic_gzip_json(directory / "result.json.gz", {"iteration": request["ordinal"]})
        raise SystemExit(4)
    if request["phase"] == "compile":
        run.publish(directory, request, "measured", metrics=CLOCKS)
    else:
        if mode != "no-start":
            run.atomic_gzip_json(directory / "start.json.gz", dict(iteration=request["ordinal"], x0=[0.]))
            run.atomic_gzip_json(directory / "verified-start.json.gz", dict(
                iteration=request["ordinal"], physical_start_sha256="fixture-physical-start",
            ))
        run.publish(directory, request, "accepted", metrics=dict(
            native_status=0, physical_objective=100., build_to_extraction_seconds=4.,
            start_sha256="different-start" if mode == "different-start" and request["revision"] == "batched"
            else "fixture-physical-start", native_options_sha256="same-options",
        ), audit={"passed": True, "residuals": {"balance": 0.}}, result={"Pg": [1.]})


def invoke(root, mode, approval, resume):
    def command_for(root, directory, unit):
        if mode == "prelaunch-refusal":
            raise ValueError("Relevant source changed before spawn")
        sleeping = mode in {"stop", "lost-supervisor"} and unit["revision"] == "batched"
        behavior = "sleep" if sleeping else mode
        executable = "/nonexistent/benchmark-rehearsal" if mode == "launch-fail" else sys.executable
        return [executable, "-m", TEST_MODULE, "worker", str(directory), behavior], dict(os.environ, **run.THREADS)

    def rss(pid):
        if mode == "unavailable":
            return None
        if mode == "rss" and len(run.directories(root)) == 1:
            return 20000.
        return 30.

    def after(directory):
        if mode == "after-publication":
            os._exit(74)

    def validate(binding):
        if mode == "new-stop":
            run.request_stop(root)

    print(run.execute(root, approval, resume=resume, command_for=command_for,
                      validate=validate, rss_reader=rss, after=after))


@pytest.fixture
def rehearsal(tmp_path):
    binding = tmp_path / "binding.json"
    work = [dict(id=f"{phase}:{side}", fixture="rehearsal", repeat=1,
                 revision=side, phase=phase) for phase in ("compile", "solve") for side in ("baseline", "batched")]
    run.atomic_immutable_json(binding, dict(work=work, settings={"limits": dict(
        rss_mib=16384., wall_seconds=10., poll_seconds=.025,
    )}))
    return tmp_path, run.sha256_path(binding)


def start(rehearsal, mode="complete", *, resume=False, approval=None):
    root, bound = rehearsal
    return subprocess.Popen([sys.executable, "-m", TEST_MODULE, "supervisor", str(root), mode,
                             approval or bound, str(int(resume))], cwd=run.ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env=dict(os.environ, **run.THREADS))


def completed(process, returncode=0):
    try:
        out, err = process.communicate(timeout=35)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise
    assert process.returncode == returncode, (out, err)
    return out


def wait_for(path, process):
    end = time.monotonic()+15
    while not path.exists() and process.poll() is None and time.monotonic() < end:
        time.sleep(.025)
    assert path.exists()


def test_double_stop_resume_and_serial_ownership(rehearsal):
    root, _ = rehearsal
    receipt = root / "attempts/attempt-000001/completion.json"
    for ordinal in (2, 3):
        process = start(rehearsal, "stop", resume=ordinal > 2)
        directory = root / "attempts" / f"attempt-{ordinal:06d}"
        wait_for(directory / "phase.json", process)
        assert receipt.exists()
        before = run.sha256_path(receipt)
        if ordinal == 2:
            completed(start(rehearsal, resume=True), 1)
        run.request_stop(root)
        assert "operator_stop" in completed(process)
        assert run.read(directory / "supervision.json")["returncode"] is not None
        assert run.status(root)["pending"][0]["id"] == "compile:batched"
        assert run.sha256_path(receipt) == before
    assert "complete" in completed(start(rehearsal, resume=True))
    progress = run.status(root)
    assert [a["ordinal"] for a in progress["attempts"]] == list(range(1, 7))
    assert progress["accepted_solves"] == 2 and progress["worker_seconds"] > 0
    assert run.sha256_path(receipt) == before
    assert "complete" in completed(start(rehearsal, resume=True))
    assert len(run.status(root)["attempts"]) == 6


def test_recover_complete_publication_even_when_progress_was_not_advanced(rehearsal):
    root, _ = rehearsal
    completed(start(rehearsal, "after-publication"), 74)
    assert not run.read(root / "progress.json")["terminal"]
    assert run.status(root)["terminal"]["compile:baseline"]["disposition"] == "measured"
    assert "complete" in completed(start(rehearsal, resume=True))
    assert len(run.status(root)["attempts"]) == 4


def test_recovers_supervisor_crash_during_a_live_attempt(rehearsal):
    root, _ = rehearsal
    process = start(rehearsal, "lost-supervisor")
    interrupted = root / "attempts/attempt-000002"
    wait_for(interrupted / "phase.json", process)
    process.kill()
    completed(process, -9)
    assert not (interrupted / "supervision.json").exists()
    # The inherited lock survives the parent until the child's pipe watcher
    # sees EOF. Wait for that proof rather than probing a potentially stale PID.
    deadline = time.monotonic() + 5
    while True:
        with (root / "supervisor.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                assert time.monotonic() < deadline
            else:
                break
        time.sleep(.025)
    assert "complete" in completed(start(rehearsal, resume=True))
    view = run.status(root)
    assert view["unknown_effort_attempts"] == 1
    assert view["attempts"][1]["disposition"] == "interrupted_supervisor"
    assert view["attempts"][1]["accounting"] == "charged_attempt_ceiling_not_measured"
    assert len(view["attempts"]) == 5


@pytest.mark.parametrize("mode,outcome", [("partial", "worker_failure"), ("launch-fail", "launch_failure"),
                                         ("unavailable", "monitoring_failure")])
def test_infrastructure_stop_needs_explicit_resume(rehearsal, mode, outcome):
    root, _ = rehearsal
    assert outcome in completed(start(rehearsal, mode))
    view = run.status(root)
    assert not view["terminal"] and view["attempts"][0]["disposition"] == outcome
    assert "complete" in completed(start(rehearsal, resume=True))
    assert len(run.status(root)["attempts"]) == 5


def test_rss_exit_is_not_retried_and_unmatched_pair_has_no_speedup(rehearsal):
    from .report import compare

    root, _ = rehearsal
    assert "complete" in completed(start(rehearsal, "rss"))
    assert "complete" in completed(start(rehearsal, resume=True))
    view = run.status(root)
    assert len(view["attempts"]) == 4
    assert view["terminal"]["compile:baseline"]["disposition"] == "rss_limit"
    compile_pair, solve_pair = compare(root, view)["pairs"]
    assert not compile_pair["comparable"] and "build_seconds" not in compile_pair
    assert solve_pair["comparable"] and solve_pair["build_to_extraction_seconds"]["baseline_over_batched"] == 1


def test_rejects_mismatched_authority_before_launch(rehearsal):
    root, _ = rehearsal
    completed(start(rehearsal, approval="0" * 64), 1)
    assert not run.directories(root)


def test_new_stop_during_resume_validation_prevents_a_launch(rehearsal):
    root, _ = rehearsal
    run.request_stop(root)
    assert "operator_stop" in completed(start(rehearsal, "new-stop", resume=True))
    assert not run.directories(root)
    assert len(run.stop_requests(root)) == 1
    assert len(list((root / "stops").glob("acknowledged-*.json"))) == 1


def test_stop_preserves_a_worker_already_exited_in_budget(rehearsal, monkeypatch):
    from types import SimpleNamespace

    root, approval = rehearsal
    first = run.read(root / "binding.json")["work"][0]
    request = dict(**first, ordinal=1, binding_sha256=approval)
    attempt = run.reserve(root, 1, request)
    clock = 0.

    class Child:
        pid = 42

        def poll(self):
            return 0 if clock else None

        def wait(self):
            return self.poll()

    def finish_then_stop(duration):
        nonlocal clock
        run.publish(attempt, request, "measured", metrics=CLOCKS)
        clock += duration
        run.request_stop(root)

    def not_a_live_child(process):
        raise AssertionError("The child had already exited; do not send a signal")

    monkeypatch.setattr(run, "time", SimpleNamespace(monotonic=lambda: clock, sleep=finish_then_stop))
    monkeypatch.setattr(run.subprocess, "Popen", lambda *args, **kwargs: Child())
    monkeypatch.setattr(run, "_terminate", not_a_live_child)
    sup = run.supervise(["fake"], attempt, root, dict(wall_seconds=1., rss_mib=16384., poll_seconds=.1),
                        {}, 0, rss_reader=lambda pid: 30.)
    assert sup["disposition"] == "exited" and run.stop_requests(root)
    assert run.status(root)["terminal"][first["id"]]["disposition"] == "measured"


def test_known_prelaunch_refusal_does_not_reserve_or_charge_a_worker(rehearsal):
    root, _ = rehearsal
    assert "prelaunch_failure" in completed(start(rehearsal, "prelaunch-refusal"))
    assert run.read(root / "outcome.json")["pending_unit"] == "compile:baseline"
    assert not run.directories(root)
    assert "complete" in completed(start(rehearsal, resume=True))
    view = run.status(root)
    assert len(view["attempts"]) == 4
    assert view["unknown_effort_attempts"] == 0
    assert view["worker_seconds"] < 40.


def test_late_exit_is_censored_even_with_a_slow_rss_read(tmp_path, monkeypatch):
    from types import SimpleNamespace

    clock = 0.

    class Child:
        pid = 42

        def poll(self):
            return 0 if clock >= 1.2 else None

        def wait(self):
            return self.poll()

    def slow_rss(pid):
        nonlocal clock
        clock = 1.6
        return 24.

    monkeypatch.setattr(run, "time", SimpleNamespace(monotonic=lambda: clock))
    monkeypatch.setattr(run.subprocess, "Popen", lambda *args, **kwargs: Child())
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    result = run.supervise(["fake"], attempt, tmp_path,
                           dict(wall_seconds=1., rss_mib=16384., poll_seconds=.25), {}, 0, rss_reader=slow_rss)
    assert result["disposition"] == "wall_limit" and result["returncode"] == 0


@pytest.mark.parametrize("mode,match", [("no-start", "lacks actual and verified"),
                                        ("different-start", "Verified start and result disagree")])
def test_no_accepted_solve_without_matching_start_receipts(rehearsal, mode, match):
    root, _ = rehearsal
    completed(start(rehearsal, mode), 1)
    with pytest.raises(ValueError, match=match):
        run.status(root)


def test_parses_native_not_adapter_or_partial_iteration(tmp_path):
    log = tmp_path / "worker.log"
    log.write_text("10 7.0 0.1\nadapter seconds 4.5\n")
    assert run.native_log(log) == dict(iterations=None, ipopt_seconds=None, exit=None)
    log.write_text("Number of Iterations....: 14\nTotal seconds in IPOPT = 1.234\nEXIT: Optimal Solution Found.\n")
    assert run.native_log(log) == dict(iterations=14, ipopt_seconds=1.234, exit="Optimal Solution Found.")


if __name__ == "__main__":
    action, folder, mode, *values = sys.argv[1:]
    if action == "worker":
        fake_worker(Path(folder), mode, int(values[0]))
    else:
        invoke(Path(folder), mode, values[0], bool(int(values[1])))
