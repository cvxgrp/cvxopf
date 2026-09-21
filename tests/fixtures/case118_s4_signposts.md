# Case118 annual SoC signposts for S4b tests

`case118_s4_signposts.npz` contains the 8,761 boundary SoC rows for four
storage devices from the retained S4 annual DC plan, plus its identity
metadata and recorded audit. It lets signpost, checkpoint, and publication
tests run in a fresh checkout without the ignored experiment archive.

Source: `experiments/case118_annual_hierarchy/results/s4_annual_outer_rated_attempt_005/outer-plan.json.gz`.
Its SHA-256 is
`6e7d88e8eed39de4a0141b0fe3c8a146fd2ae298a3d3ddc2768ae57247e87031`,
also frozen in `s4b_manifest.S4_OUTER_ARCHIVE_SHA256`. The test loader checks
that identity, and `StreamingOuterPlan` verifies the original signpost hash.
The full generator and network primal is omitted; this fixture does not
replace a physical audit of the source plan. No optimization is rerun to
extract it.

To regenerate locally with the original archive available, run this Python
code from the repository root using `uv run --extra dev python`:

```python
from dataclasses import asdict
import json
from pathlib import Path

import numpy as np

from experiments.case118_annual_hierarchy import run_s4b

outer = run_s4b._outer()  # verifies the retained source archive
metadata = {
    key: value
    for key, value in asdict(outer).items()
    if key not in {"build", "result", "boundary_soc_mwh", "global_boundary_indices"}
}
metadata["source_archive_sha256"] = run_s4b.sha256_path(
    run_s4b.S4_OUTER_ARCHIVE_PATH
)
np.savez_compressed(
    Path("tests/fixtures/case118_s4_signposts.npz"),
    boundary_soc_mwh=outer.boundary_soc_mwh,
    metadata=json.dumps(metadata),
)
```

The three tests that audit complete historical archives remain local checks:
they skip when their run directory is absent, but still fail if a present
archive is incomplete or corrupt. Context-validation tests use temporary
reference files and controlled Git identities, so they also work in shallow
CI checkouts.
