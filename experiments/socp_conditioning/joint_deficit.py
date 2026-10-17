"""Two serial deficit solves with the unchanged surplus joint-scaling rule."""

from pathlib import Path
from unittest.mock import patch

from experiments.socp_conditioning import cone_scaling as c
from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import joint_scaling as j

ARMS = ("shedding_baseline", "shedding_scaled")
FROZEN_RULE_SHA256 = "f1c9eb1ca4308696baf32ccde8a137c28873b57bf46f7459ef53ae7bf3c9ce25"


def context():
    if d.sha(j.__file__) != FROZEN_RULE_SHA256:
        raise ValueError("joint scaling rule changed since surplus")
    base = j.context()
    return base | {
        "experiment": "unchanged_joint_scaling_large_deficit",
        "scenario": "E3 arm 006, Large deficit, [1165,1189)",
        "arms": list(ARMS),
        "additional_sources": base["additional_sources"]
        | {str(Path(__file__).relative_to(d.ROOT)): d.sha(__file__)},
    }


def worker(directory, reference, label):
    if label not in ARMS:
        raise ValueError("only the declared deficit pair is authorized")

    def verify(data, inverse, input_hash, fixed):
        if fixed:
            raise ValueError("deficit shedding policy must remain enabled")
        if label == ARMS[0]:
            # load_case has independently reconstructed/hash-checked E3 inputs.
            # E3 used squared device cones, so it has a different canonical graph.
            return dict(
                kind="new_normalized_cone_baseline_from_verified_E3_inputs",
                input_sha256=input_hash,
                historical_canonical_equality_claimed=False,
            )
        return c.verify_reference(
            data,
            inverse,
            input_hash,
            fixed,
            directory=directory.parent / ARMS[0],
        )

    BASE_WORKER(
        directory,
        reference,
        label,
        scaling_strategy=j.joint_scales,
        case_name="deficit",
        reference_validator=verify,
    )


BASE_WORKER = c.worker


if __name__ == "__main__":
    # The shared CLI dispatches to this module in each fresh worker. Preserve
    # the original callable so the temporary dispatcher cannot recurse.
    with patch.object(c, "context", context), patch.object(c, "ARMS", ARMS):
        with patch.object(c, "worker", worker):
            c.main(worker_module=__spec__.name)
