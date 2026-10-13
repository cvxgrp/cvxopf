"""Evidence-only regressions for the retained five-/six-bus back-tests."""

from collections import Counter
from decimal import Decimal
from fractions import Fraction
from itertools import combinations
import json
from pathlib import Path

import pytest

from experiments.tracy_socp_transfer_preflight import long_loops as long
from experiments.tracy_socp_transfer_preflight import historical_replay as h


@pytest.mark.parametrize("codes", ["", "N", "NNNBUNNCC", "N" * 500 + "B" + "N" * 70])
def test_status_encoding_is_lossless(codes):
    assert long.decode_status(long.encode_status(codes)) == codes


@pytest.mark.parametrize("maximum", [5, 6])
def test_selected_long_loop_evidence(maximum):
    path = long.m.p.HERE / f"LONG_LOOPS_{maximum}_EVIDENCE.json"
    if not path.exists():
        pytest.skip("selected long-loop evidence not saved yet")
    report = json.loads(path.read_text())
    policy = report["policy"]
    assert policy["max_cycle_length"] == maximum
    assert policy["max_boxes_per_triggered_loop_hour"] == 64
    assert policy["report_only"] and policy["new_optimizer_calls"] == 0
    assert report["historical_bytes_unchanged"] and report["existing_results_unchanged"]
    assert report["sources"]["long_loop_driver_sha256"] == h.digest(Path(long.__file__))
    for source, checksum in report["existing_files_verified"].items():
        assert h.digest(h.ROOT / source) == checksum
    catalog = report["topology"]["cycle_catalog"]
    assert len(catalog) == len(set(map(tuple, catalog)))
    assert all(2 <= len(c) <= maximum and len(c) == len(set(c)) for c in catalog)
    assert sum(len(c) == maximum for c in catalog) > 0
    assert sum(len(c) == 2 for c in catalog) == 7
    summary = report["summary"]
    assert summary["historical_outcomes"] == {
        "compatible": 15,
        "native_rejection": 2,
        "timeout": 1,
    }
    assert (
        summary["compatible_witness_contradictions"]
        == summary["control_contradictions"]
        == 0
    )
    assert len(report["transfers"]) == 18 and len(report["ac_controls"]) == 6
    assert summary["donor_hours"] == 432 and summary["control_hours"] == 144
    for cohort in ("transfers", "ac_controls"):
        counts = Counter()
        for row in report[cohort]:
            assert len(row["hours"]) == 24
            for hour in row["hours"]:
                codes = long.decode_status(hour["loop_status_runs"])
                assert len(codes) == len(catalog)
                counts.update(codes)
                for detail in hour["loops"]:
                    assert detail["cycle"] == catalog[detail["cycle_index"]]
                    assert detail["tested_boxes"] <= 64
                    assert codes[detail["cycle_index"]] == (
                        "C" if detail["status"] == "certified_incompatible" else "B"
                    )
                    if detail["status"] == "certified_incompatible":
                        assert detail["unresolved_boxes"] == 0
                        boxes = [
                            [
                                [(Fraction(Decimal(x))) for x in pair]
                                for pair in leaf["voltage_boxes"]
                            ]
                            for leaf in detail["certificate_leaves"]
                        ]

                        def volume(box):
                            result = Fraction(1)
                            for lo, hi in box:
                                result *= hi - lo
                            return result

                        root = [
                            [min(b[j][0] for b in boxes), max(b[j][1] for b in boxes)]
                            for j in range(len(detail["cycle"]))
                        ]
                        assert sum(map(volume, boxes)) == volume(root)
                        for left, right in combinations(boxes, 2):
                            assert any(
                                min(x[1], y[1]) <= max(x[0], y[0])
                                for x, y in zip(left, right)
                            )
                        for leaf in detail["certificate_leaves"]:
                            if leaf["reason"] == "cycle_nonclosure":
                                assert (
                                    long.m.scanner.v1.winding_gap(
                                        long.m.scanner.I(
                                            *(
                                                Decimal(x)
                                                for x in leaf[
                                                    "closure_interval_radians"
                                                ]
                                            )
                                        )
                                    )
                                    > 0
                                )
            if cohort == "ac_controls" or row["historical_outcome"]["compatible"]:
                assert row["verdict"] == "unresolved"
        assert sum(counts.values()) == len(report[cohort]) * 24 * len(catalog)
