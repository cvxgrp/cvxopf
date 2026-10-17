"""Non-solving guards for the experimental native KKT scaling replay."""

import os

import pytest

from experiments.socp_conditioning import native_kkt_scaling as k


def test_control_clears_inherited_scaling_and_dump_environment(monkeypatch):
    for name in ("CVXOPF_TRACE", "CVXOPF_KKT_DIR", "CVXOPF_KKT_SCALING"):
        monkeypatch.setenv(name, "inherited")
    with k.native_environment("control"):
        assert not any(
            name in os.environ
            for name in ("CVXOPF_TRACE", "CVXOPF_KKT_DIR", "CVXOPF_KKT_SCALING")
        )
    assert os.environ["CVXOPF_KKT_SCALING"] == "inherited"
    with k.native_environment("five_pass"):
        assert os.environ["CVXOPF_KKT_SCALING"] == "5"
        assert "CVXOPF_KKT_DIR" not in os.environ
    with pytest.raises(ValueError):
        with k.native_environment("ten_pass"):
            pass


def record():
    vectors = {key: [1.0] for key in ("x", "s", "z")}
    return dict(
        exception=None,
        context={},
        context_after={},
        optimizer_calls=1,
        native_input_sha256=k.INPUT_SHA,
        raw_clarabel=vectors,
    )


def test_disabled_control_is_exact_gate_but_enabled_result_is_not():
    prior = record()
    assert k.validate_arm(prior, {}, prior, "control")
    changed = record() | dict(raw_clarabel={"x": [2.0], "s": [1.0], "z": [1.0]})
    with pytest.raises(ValueError, match="disabled control"):
        k.validate_arm(changed, {}, prior, "control")
    assert not k.validate_arm(changed, {}, prior, "five_pass")


@pytest.mark.parametrize(
    "change",
    [
        dict(native_input_sha256="altered"),
        dict(context_after={"changed": True}),
        dict(optimizer_calls=2),
        dict(exception="failed reconstruction"),
    ],
)
def test_identity_changes_rejected(change):
    with pytest.raises(ValueError, match="replay identity"):
        k.validate_arm(record() | change, {}, record(), "five_pass")
