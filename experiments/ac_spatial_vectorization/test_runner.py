"""Fresh-process rehearsals for the real serial supervisor with fake workers."""

import fcntl
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread
import time
from unittest.mock import patch

import pytest

from . import run


TEST_MODULE = "experiments.ac_spatial_vectorization.test_runner"
CLOCKS = dict(build_seconds=.1, preparation_seconds=.1, reduction_seconds=.2, derivative_seconds=.3)


def fake_worker(directory, mode, parent_fd):
    def watch():
        with os.fdopen(parent_fd, "rb") as pipe:
            pipe.read(1)
        os._exit(75)

    if mode != "host-denied-group":
        Thread(target=watch, daemon=True).start()
    request = run.read(directory / "request.json")
    run.atomic_json(directory / "phase.json", {"phase": "fake"})
    # Give the real supervisor time to observe the short fake child.
    time.sleep(.15)
    if mode in {"sleep", "host-denied-group"}:
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
    if mode == "host-exit-race" and request["ordinal"] == 1:
        deadline = time.monotonic() + 5.
        while not (directory / "release-exit").exists() and time.monotonic() < deadline:
            time.sleep(.005)
        assert (directory / "release-exit").exists()


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
        if mode.startswith("host-"):
            observed = run.read_rss(pid)
            first = root / "attempts/attempt-000001"
            if mode == "host-denied-group" and (first / "phase.json").exists():
                return None
            if (mode == "host-exit-race" and (first / "completion.json").exists() and
                    not (first / "release-exit").exists()):
                (first / "release-exit").touch()
                return None
            return observed
        return 30.

    def after(directory):
        if mode == "after-publication":
            os._exit(74)

    def validate(binding):
        if mode == "new-stop":
            run.request_stop(root)

    def execute():
        print(run.execute(root, approval, resume=resume, command_for=command_for,
                          validate=validate, rss_reader=rss, after=after))

    if mode == "host-denied-group":
        with patch.object(run.os, "killpg", side_effect=PermissionError("rehearsed group denial")):
            execute()
    else:
        execute()


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

    def not_a_live_child(*args):
        raise AssertionError("The child had already exited; do not send a signal")

    monkeypatch.setattr(run, "time", SimpleNamespace(monotonic=lambda: clock, sleep=finish_then_stop))
    monkeypatch.setattr(run.subprocess, "Popen", lambda *args, **kwargs: Child())
    monkeypatch.setattr(run.os, "killpg", not_a_live_child)
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


@pytest.mark.parametrize("late", [False, True])
def test_missing_rss_at_clean_exit_respects_existing_deadline(rehearsal, monkeypatch, late):
    from types import SimpleNamespace

    root, approval = rehearsal
    unit = run.read(root / "binding.json")["work"][0]
    request = dict(**unit, ordinal=1, binding_sha256=approval)
    attempt = run.reserve(root, 1, request)
    clock = 0.

    class Child:
        pid = 42
        exited = False

        def poll(self):
            return 0 if self.exited else None

        def wait(self, timeout):
            nonlocal clock
            assert .09 < timeout < .11
            clock += .15 if late else .05
            run.publish(attempt, request, "measured", metrics=CLOCKS)
            self.exited = True
            return 0

    def sleep(duration):
        nonlocal clock
        clock += duration

    def rss(pid):
        if clock:
            raise PermissionError("RSS unavailable at exit")
        return 32.

    monkeypatch.setattr(run, "time", SimpleNamespace(monotonic=lambda: clock, sleep=sleep))
    monkeypatch.setattr(run.subprocess, "Popen", lambda *args, **kwargs: Child())
    sup = run.supervise(["fake"], attempt, root, dict(wall_seconds=1., rss_mib=16384., poll_seconds=.9),
                        {}, 0, rss_reader=rss)
    assert sup["samples"] == 1 and sup["child_reaped"]
    assert sup["monitoring_gap"]["observed_exit"] and "PermissionError" in sup["monitoring_gap"]["error"]
    assert run.status(root)["terminal"][unit["id"]]["disposition"] == ("wall_limit" if late else "measured")


@pytest.mark.parametrize("trigger", ["rss_limit", "wall_limit", "monitoring_failure"])
def test_cleanup_failure_stops_and_resume_preserves_censoring(rehearsal, monkeypatch, trigger):
    root, approval = rehearsal
    events = []
    clock = 0.

    def denied(pid, sig):
        events.append(("group", sig))
        raise PermissionError("group signal denied")

    class Child:
        pid = 42

        def poll(self):
            return None

        def send_signal(self, sig):
            events.append(("child", sig))
            raise PermissionError("child signal denied")

        def wait(self, timeout):
            assert timeout in {run.RSS_EXIT_GRACE_SECONDS, run.CLEANUP_WAIT_SECONDS}
            raise subprocess.TimeoutExpired("fake", timeout)

    def rss(pid):
        nonlocal clock
        if trigger == "wall_limit":
            clock += 10.
        return 20000. if trigger == "rss_limit" else None

    with monkeypatch.context() as injected:
        injected.setattr(run.os, "killpg", denied)
        injected.setattr(run.subprocess, "Popen", lambda *args, **kwargs: Child())
        injected.setattr(run.time, "monotonic", lambda: clock)
        result = run.execute(root, approval, resume=False, command_for=lambda *args: (["fake"], {}),
                             validate=lambda binding: None, rss_reader=rss)
    assert result == "cleanup_failure"
    assert [kind for kind, sig in events] == ["group", "child", "group", "child"]
    view = run.status(root)
    assert len(view["attempts"]) == 1
    attempt = view["attempts"][0]
    censored = trigger in run.RESOURCE_LIMITS
    assert (attempt["id"] in view["terminal"]) is censored
    assert attempt["disposition"] == (trigger if censored else "cleanup_failure")
    assert attempt["cleanup_failed"] and run.read(root / "outcome.json")["supervisor_disposition"] == trigger
    assert view["unknown_effort_attempts"] == 1 and view["worker_seconds"] == 10.
    receipt = root / attempt["path"] / "supervision.json"
    sup = run.read(receipt)
    assert sup["disposition"] == trigger and sup["child_reaped"] is False
    assert sup["returncode"] is None and len(sup["cleanup_errors"]) == 6

    saved = run.sha256_path(receipt)
    # The fake child has no inherited lock; this models resume after that lock is released.
    assert "complete" in completed(start(rehearsal, resume=True))
    final = run.status(root)
    assert final["attempts"][1]["id"] == ("compile:batched" if censored else "compile:baseline")
    assert final["terminal"]["compile:baseline"]["disposition"] == (trigger if censored else "measured")
    assert run.sha256_path(receipt) == saved


@pytest.mark.skipif(os.environ.get("CVXOPF_TEST_HOST_PROCESS") != "1",
                    reason="requires the process permissions of the real benchmark launch")
def test_real_host_rss_exit_race_accepts_published_worker(rehearsal):
    root, _ = rehearsal
    assert "complete" in completed(start(rehearsal, "host-exit-race"))
    attempt = run.status(root)["attempts"][0]
    assert attempt["samples"] > 0 and attempt["child_reaped"] and attempt["returncode"] == 0
    assert attempt["monitoring_gap"]["observed_exit"] and attempt["disposition"] == "measured"


@pytest.mark.skipif(os.environ.get("CVXOPF_TEST_HOST_PROCESS") != "1",
                    reason="requires the process permissions of the real benchmark launch")
def test_real_host_rss_and_owned_child_signal_fallback(rehearsal):
    root, _ = rehearsal
    assert "monitoring_failure" in completed(start(rehearsal, "host-denied-group"))
    attempt = run.status(root)["attempts"][0]
    assert attempt["samples"] > 0 and attempt["child_reaped"] and attempt["returncode"] < 0
    assert attempt["cleanup_errors"] == ["group SIGTERM: PermissionError: rehearsed group denial"]
    assert "complete" in completed(start(rehearsal, "host-rss", resume=True))
    assert len(run.status(root)["terminal"]) == 4


def test_continuation_keeps_original_measurement_and_worker_only_archive(tmp_path, rehearsal, monkeypatch):
    from . import binding
    from .report import compare

    old = tmp_path / "benchmark_original"
    old.mkdir()
    (old / "supervisor.lock").touch()
    (old / "case500.m").touch()
    (old / "sources").mkdir()
    prior = run.read(rehearsal[0] / "binding.json")
    supervisor = "experiments/ac_spatial_vectorization/run.py"
    worker = "experiments/ac_spatial_vectorization/worker.py"
    prior["harness"] = {supervisor: "old", worker: "unchanged"}
    run.atomic_immutable_json(old / "binding.json", prior)
    run.atomic_immutable_json(old / "outcome.json", {"outcome": "infrastructure_failure"})
    old_sha = run.sha256_path(old / "binding.json")
    for ordinal, unit in enumerate(prior["work"][:2], 1):
        request = dict(**unit, ordinal=ordinal, binding_sha256=old_sha)
        directory = run.reserve(old, ordinal, request)
        run.publish(directory, request, "measured", metrics=CLOCKS)
        if ordinal == 1:
            run.atomic_immutable_json(directory / "supervision.json", dict(
                disposition="exited", returncode=0, samples=2, peak_rss_mib=30., worker_seconds=.2,
            ))
    new = tmp_path / "benchmark_continuation"
    original = run.predecessor_evidence(old)
    monkeypatch.setattr(binding, "verify_context", lambda *args: None)
    monkeypatch.setattr(binding, "harness_sources", lambda: {supervisor: "new", worker: "changed"})
    with pytest.raises(ValueError, match="changes scientific execution code"):
        binding.bind_continuation(new, old, old_sha)
    monkeypatch.setattr(binding, "harness_sources", lambda: {supervisor: "new", worker: "unchanged"})
    with pytest.raises(ValueError, match="Predecessor binding SHA-256"):
        binding.bind_continuation(new, old, "0" * 64)

    bound = binding.bind_continuation(new, old, old_sha)
    initial = run.status(new)
    assert initial["worker_seconds"] == 10.2 and initial["unknown_effort_attempts"] == 1
    assert initial["terminal"]["compile:baseline"]["inherited"]
    assert initial["pending"][0]["id"] == "compile:batched"
    process = start((new, bound), "stop")
    wait_for(new / "attempts/attempt-000003/phase.json", process)
    run.request_stop(new)
    assert "operator_stop" in completed(process)
    assert "complete" in completed(start((new, bound), resume=True))
    final = run.status(new)
    assert [a["ordinal"] for a in final["attempts"]] == list(range(1, 7))
    assert final["attempts"][1]["disposition"] == "interrupted_supervisor"
    assert compare(new, final)["pairs"][0]["comparable"]
    assert run.predecessor_evidence(old) == original
    (old / "attempts/attempt-000002/phase.json").touch()
    with pytest.raises(ValueError, match="Predecessor evidence changed"):
        run.status(new)


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
