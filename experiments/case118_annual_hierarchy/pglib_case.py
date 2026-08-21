"""Strict, provenance-checked loader for the annual experiment's PGLib case."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Mapping

import numpy as np

from cvxopf import Load


SOURCE_REVISION = "dc6be4b2f85ca0e776952ec22cbd4c22396ea5a3"
SOURCE_DIRECTORY = Path(__file__).with_name("source")
SOURCE_CASE_PATH = SOURCE_DIRECTORY / "pglib_opf_case118_ieee.m"
SOURCE_LICENSE_PATH = SOURCE_DIRECTORY / "PGLIB_LICENSE"
MANIFEST_PATH = SOURCE_DIRECTORY / "manifest.json"

_EXPECTED_SHAPES = {
    "bus": (118, 13),
    "gen": (54, 10),
    "gencost": (54, 7),
    "branch": (186, 13),
}
_CONVERTED_SHAPES = {**_EXPECTED_SHAPES, "gen": (54, 21)}
_MATRIX_PATTERN = re.compile(
    r"mpc\.(?P<name>bus|gen|gencost|branch)\s*=\s*\["
    r"(?P<body>.*?)\n\s*\];",
    flags=re.DOTALL,
)


def file_sha256(path: Path) -> str:
    """Return the SHA-256 digest of one source artifact."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def array_sha256(values: np.ndarray) -> str:
    """Hash a numeric array using a portable little-endian float64 form."""
    array = np.ascontiguousarray(values, dtype="<f8")
    header = f"float64-le|shape={array.shape}|".encode()
    return hashlib.sha256(header + array.tobytes(order="C")).hexdigest()


def _parse_matrix(name: str, body: str) -> np.ndarray:
    rows: list[list[float]] = []
    for raw_line in body.splitlines():
        data = raw_line.split("%", maxsplit=1)[0].strip()
        if not data:
            continue
        if not data.endswith(";"):
            raise ValueError(f"PGLib {name} row lacks a semicolon: {raw_line!r}")
        fields = data[:-1].split()
        try:
            row = [float(value) for value in fields]
        except ValueError as exc:
            raise ValueError(f"PGLib {name} contains a nonnumeric row") from exc
        rows.append(row)
    widths = {len(row) for row in rows}
    if len(widths) != 1:
        raise ValueError(f"PGLib {name} rows do not have a common width")
    array = np.asarray(rows, dtype=float)
    if array.shape != _EXPECTED_SHAPES[name]:
        raise ValueError(
            f"PGLib {name} has shape {array.shape}, expected "
            f"{_EXPECTED_SHAPES[name]}"
        )
    if not np.isfinite(array).all():
        raise ValueError(f"PGLib {name} contains nonfinite values")
    return array


def parse_pglib_case118(source: str) -> dict[str, object]:
    """Parse the pinned, ordinary PGLib IEEE-118 MATPOWER source."""
    function = re.search(r"^function mpc = (?P<name>\w+)\s*$", source, re.MULTILINE)
    if function is None or function.group("name") != "pglib_opf_case118_ieee":
        raise ValueError("unexpected PGLib case function name")
    version = re.search(r"mpc\.version\s*=\s*'(?P<value>[^']+)'\s*;", source)
    if version is None or version.group("value") != "2":
        raise ValueError("PGLib case must use MATPOWER version 2")
    base = re.search(r"mpc\.baseMVA\s*=\s*(?P<value>[^;]+);", source)
    if base is None:
        raise ValueError("PGLib case does not define baseMVA")
    try:
        base_mva = float(base.group("value"))
    except ValueError as exc:
        raise ValueError("PGLib baseMVA must be numeric") from exc
    if not np.isfinite(base_mva) or base_mva <= 0:
        raise ValueError("PGLib baseMVA must be finite and positive")

    matches = list(_MATRIX_PATTERN.finditer(source))
    names = [match.group("name") for match in matches]
    if names != ["bus", "gen", "gencost", "branch"]:
        raise ValueError(f"unexpected PGLib matrix sequence {names}")
    matrices = {
        match.group("name"): _parse_matrix(
            match.group("name"), match.group("body")
        )
        for match in matches
    }
    return {
        "version": "2",
        "baseMVA": base_mva,
        **matrices,
    }


def convert_pglib_case118(source_case: Mapping[str, object]) -> dict[str, object]:
    """Convert the pinned PGLib source arrays to cvxopf's case contract.

    PGLib provides the ten required MATPOWER generator columns. cvxopf's
    historical case validator requires the complete 21-column MATPOWER
    generator layout, although it does not model columns 10--20. The
    conversion therefore preserves columns 0--9 exactly and fills those
    optional columns with zero. No other numeric case datum is changed.
    """
    converted = deepcopy(dict(source_case))
    source_gen = np.asarray(source_case["gen"], dtype=float)
    converted_gen = np.zeros(_CONVERTED_SHAPES["gen"], dtype=float)
    converted_gen[:, : source_gen.shape[1]] = source_gen
    converted["gen"] = converted_gen
    return converted


def _verify_manifest(case: Mapping[str, object], manifest: Mapping[str, object]) -> None:
    if manifest.get("source_revision") != SOURCE_REVISION:
        raise ValueError("PGLib manifest source revision mismatch")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise ValueError("PGLib manifest artifacts must be a mapping")
    paths = {
        "case": SOURCE_CASE_PATH,
        "license": SOURCE_LICENSE_PATH,
    }
    for name, path in paths.items():
        specification = artifacts.get(name)
        if not isinstance(specification, Mapping):
            raise ValueError(f"PGLib manifest lacks {name} artifact")
        if file_sha256(path) != specification.get("sha256"):
            raise ValueError(f"PGLib {name} file hash mismatch")

    arrays = manifest.get("arrays")
    if not isinstance(arrays, Mapping):
        raise ValueError("PGLib manifest arrays must be a mapping")
    for name, shape in _EXPECTED_SHAPES.items():
        specification = arrays.get(name)
        if not isinstance(specification, Mapping):
            raise ValueError(f"PGLib manifest lacks {name} array")
        values = np.asarray(case[name], dtype=float)
        if list(shape) != specification.get("shape"):
            raise ValueError(f"PGLib {name} manifest shape mismatch")
        if array_sha256(values) != specification.get("sha256"):
            raise ValueError(f"PGLib {name} parsed-array hash mismatch")

    converted = convert_pglib_case118(case)
    converted_arrays = manifest.get("converted_arrays")
    if not isinstance(converted_arrays, Mapping):
        raise ValueError("PGLib manifest converted_arrays must be a mapping")
    for name, shape in _CONVERTED_SHAPES.items():
        specification = converted_arrays.get(name)
        if not isinstance(specification, Mapping):
            raise ValueError(f"PGLib manifest lacks converted {name} array")
        values = np.asarray(converted[name], dtype=float)
        if list(shape) != specification.get("shape"):
            raise ValueError(f"PGLib converted {name} manifest shape mismatch")
        if array_sha256(values) != specification.get("sha256"):
            raise ValueError(f"PGLib converted {name} array hash mismatch")


def load_pglib_case118() -> dict[str, object]:
    """Load the pinned source and reject source or conversion drift."""
    manifest = json.loads(MANIFEST_PATH.read_text())
    case = parse_pglib_case118(SOURCE_CASE_PATH.read_text())
    _verify_manifest(case, manifest)
    return convert_pglib_case118(case)


def make_effectively_unlimited_case(
    case: Mapping[str, object],
) -> dict[str, object]:
    """Copy a converted PGLib case and change only ``rateA`` to zero."""
    copied = deepcopy(dict(case))
    branch = np.asarray(copied["branch"], dtype=float).copy()
    branch[:, 5] = 0.0
    copied["branch"] = branch
    return copied


def loads_from_pglib_case(case: Mapping[str, object]) -> tuple[Load, ...]:
    """Materialize one fixed, nonsheddable load per nonzero-demand bus.

    Device order follows the source bus-table order and IDs use external bus
    numbers, so prepared time-series columns have a deterministic identity
    contract. A numerical zero reactive value remains an explicitly defined
    channel for active-only source rows.
    """
    bus = np.asarray(case["bus"], dtype=float)
    selected = (bus[:, 2] != 0.0) | (bus[:, 3] != 0.0)
    return tuple(
        Load(
            bus=int(row[0]),
            p_load_mw=float(row[2]),
            q_load_mvar=float(row[3]),
            device_id=f"load_bus_{int(row[0])}",
        )
        for row in bus[selected]
    )
