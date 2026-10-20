"""Publication analysis is non-solving and preserves unsuccessful outcomes."""
import csv
import json

import clarabel
import cvxpy as cp
import cyipopt
import pytest

from experiments.numerical_preparation import plot_matched_comparison as p


@pytest.fixture(autouse=True)
def no_solves(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("publication test invoked an optimizer")
    monkeypatch.setattr(clarabel, "DefaultSolver", forbidden)
    monkeypatch.setattr(cyipopt, "Problem", forbidden)
    monkeypatch.setattr(cp.Problem, "solve", forbidden)
    monkeypatch.setattr(p.model.IPOPT, "solve_via_data", forbidden)


def snapshot(tmp_path):
    source = tmp_path / "raw"
    source.mkdir()
    (source / "binding.json").write_text("immutable evidence")
    rows = []
    for form in ("ac", "socp"):
        for case, (start, horizon) in enumerate(p.CASES):
            for treatment in ("baseline", "prepared"):
                state = "accepted"
                if treatment == "baseline":
                    if form == "ac" and case == 1:
                        state = "timeout"
                    elif form == "socp" and case != 2:
                        state = "native_rejected"
                public = 10. if form == "ac" else .2
                whole = public + 2
                if state == "timeout":
                    public, whole = None, 3600.2
                rows.append(dict(arm=dict(id=len(rows)+1, pair=case, start=start, T=horizon,
                    formulation=form, scenario="synthetic", treatment=treatment),
                    accepted=state == "accepted", outcome=state,
                    public_solve_seconds=public, supervised_seconds=whole,
                    all_attempt_supervised_seconds=whole,
                    physical_cost=0. if state == "accepted" else None))
    return dict(execution_root=str(source), independently_replayed=True,
                raw_tree_sha256=p.tree_hashes(source), analysis_sources={}, rows=rows)


def test_outcomes_and_cost_floor_do_not_upgrade_failures():
    base = dict(arm={"id": 1}, accepted=False, disposition="completed",
                worker_classification="native_rejected")
    assert p.outcome(base) == "native_rejected"
    assert p.outcome(base | {"disposition": "wall_limit"}) == "timeout"
    assert p.outcome(base | {"worker_classification": "audit_rejected"}) == "audit_rejected"
    with pytest.raises(ValueError, match="nonterminal"):
        p.outcome(base | {"worker_classification": None})
    assert p.cost_position(dict(outcome="timeout", physical_cost=None)) is None
    assert p.cost_position(dict(outcome="accepted", physical_cost=0.)) == p.NEAR_ZERO
    with pytest.raises(ValueError, match="finite"):
        p.cost_position(dict(outcome="accepted", physical_cost=float("nan")))


def test_render_retains_all_arms_and_raw_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))
    data = snapshot(tmp_path)
    source = tmp_path / "raw"
    before = p.tree_hashes(source)
    output = p.render(data, tmp_path / "figures")
    assert p.tree_hashes(source) == before
    with (output / "all_arms.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 16
    assert sum(r["outcome"] == "accepted" for r in rows) == 12
    timeout = next(r for r in rows if r["outcome"] == "timeout")
    assert timeout["public_solve_seconds"] == timeout["physical_cost"] == ""
    assert float(timeout["supervised_seconds"]) == 3600.2
    assert all(r["physical_cost"] == "" for r in rows if r["outcome"] != "accepted")
    assert any(r["physical_cost"] == "0.0" for r in rows if r["outcome"] == "accepted")
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["raw_tree_sha256"] == before
    assert len(manifest["files"]) == 8
    assert all(p.model.digest(output / name) == digest for name, digest in manifest["files"].items())
    for stem in ("matched_timing", "matched_physical_cost"):
        for extension in ("pdf", "svg", "png"):
            assert (output / f"{stem}.{extension}").stat().st_size > 1000
    with pytest.raises(FileExistsError):
        p.render(data, output)
    with pytest.raises(ValueError, match="separate"):
        p.render(data, source / "figures")


def test_native_summary_preserves_both_residual_scales(tmp_path):
    log = tmp_path / "worker.log"
    log.write_text("Number of Iterations....: 119\n"
                   "Dual infeasibility......: 1.8e-08 2.3e-06\n"
                   "Overall NLP error.......: 1.8e-08 2.3e-06\n"
                   "EXIT: Optimal Solution Found.\n")
    result = p.log_summary(log)
    assert result["iterations_from_log"] == 119
    assert result["message"] == "EXIT: Optimal Solution Found."
    assert result["ipopt_final_summary"]["Dual infeasibility"] == dict(scaled=1.8e-8, unscaled=2.3e-6)
    log.write_text("Terminated with status = InsufficientProgress\n")
    assert p.log_summary(log)["iterations_from_log"] is None
    assert "InsufficientProgress" in p.log_summary(log)["message"]
