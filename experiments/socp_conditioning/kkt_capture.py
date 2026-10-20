"""Capture failing native linear systems; preserve the exact replay gate."""

from contextlib import contextmanager
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import native_step_probe as n

BASE_CONTEXT = n.context
BASE_INSTRUMENTATION = n.instrumentation


def context():
    base = BASE_CONTEXT()
    return base | dict(
        experiment="failing_native_KKT_capture",
        additional_sources=base["additional_sources"]
        | {str(Path(__file__).relative_to(d.ROOT)): d.sha(__file__)},
    )


@contextmanager
def instrumentation(solver, directory, record, *, mode):
    # Same serial, bounded native replay. Only the traced arm writes snapshots.
    with patch.dict(os.environ, {"CVXOPF_KKT_DIR": str(directory)}):
        with BASE_INSTRUMENTATION(solver, directory, record, mode=mode):
            yield
    record["kkt_snapshots"] = {
        p.name: d.sha(p) for p in sorted(directory.glob("kkt-*.json"))
    }


if __name__ == "__main__":
    with (
        patch.object(n, "context", context),
        patch.object(n, "instrumentation", instrumentation),
        patch.object(n, "__spec__", SimpleNamespace(name=__spec__.name)),
    ):
        n.main()
