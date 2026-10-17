"""Offline audit of retained canonical vectors; never imports or calls a solver.

Run with --root results/sweep_001 and optionally --output a new JSON file.
Clarabel 0.11.1 residual normalization is reconstructed from its tagged info.rs.
All original archives remain untouched; epigraph tightening is in-memory only.
"""

import argparse
import ast
import gzip
import hashlib
import json
import re
from decimal import Decimal, localcontext
from pathlib import Path

import numpy as np
from scipy import sparse


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inf(v):
    return float(np.max(np.abs(v), initial=0))


def indices(v):
    return np.arange(v["offset"], v["offset"] + int(np.prod(v["shape"])))


def cone_layout(text, rows):
    match = re.fullmatch(
        r"(\d+) equalities, (\d+) inequalities, 0 exponential cones,\s*"
        r"SOC constraints: (\[[\d, ]*\]), PSD constraints: \[\],\s*"
        r"3d power cones \[\], \[\]\.",
        text,
    )
    if match is None:
        raise ValueError("unsupported cone layout")
    zero, nonneg = int(match[1]), int(match[2])
    soc = ast.literal_eval(match[3])
    if zero + nonneg + sum(soc) != rows:
        raise ValueError("cone dimensions do not cover all rows")
    return zero, nonneg, soc


def cone_errors(v, layout, dual=False):
    zero, nonneg, soc = layout
    pos = zero + nonneg
    errors = []
    for size in soc:
        block = v[pos : pos + size]
        errors.append(max(0.0, float(np.linalg.norm(block[1:]) - block[0])))
        pos += size
    return dict(
        equality_inf=0.0 if dual else inf(v[:zero]),
        nonnegative_violation=max(0.0, float(-np.min(v[zero : zero + nonneg]))),
        soc_violation=max(errors, default=0.0),
    )


def storage_epigraph(A, P, c, rhs, variables, layout):
    """Identify abs(b) by its exact two-row structure, not generated names."""
    bvar = next(v for v in variables if v["name"] == "b")
    bi = indices(bvar)
    candidates = [
        v
        for v in variables
        if v["name"].startswith("var") and v["shape"] == bvar["shape"]
    ]
    valid = []
    csr = A.tocsr()
    for var in candidates:
        ti = indices(var)
        pairs = []
        for t, b in zip(ti, bi, strict=True):
            col = A.getcol(t)
            rr = col.indices
            if (
                len(rr) != 2
                or not np.all(col.data == -1)
                or not np.all((rr >= layout[0]) & (rr < sum(layout[:2])))
                or np.any(rhs[rr] != 0)
                or c[t] <= 0
                or P.getcol(t).nnz
                or P.getrow(t).nnz
            ):
                break
            signs = []
            for row in rr:
                entries = dict(zip(csr.getrow(row).indices, csr.getrow(row).data))
                if set(entries) != {int(t), int(b)} or abs(entries[b]) != 1:
                    break
                signs.append(entries[b])
            if sorted(signs) != [-1, 1]:
                break
            pairs.append(list(rr))
        if len(pairs) == len(ti):
            valid.append((var, ti, bi, np.asarray(pairs)))
    if len(valid) != 1:
        raise ValueError("expected exactly one verified storage abs epigraph")
    return valid[0]


def fixed_box_multipliers(A, rhs, z, variables, layout):
    """Locate exact coincident unary bounds and their contribution to ||z||."""
    csr = A.tocsr()
    unary = np.diff(csr.indptr) == 1
    result = {}
    for var in variables:
        rows, count = [], 0
        for col in indices(var):
            column = A.getcol(col)
            bounds = [
                (row, val, rhs[row] / val)
                for row, val in zip(column.indices, column.data, strict=True)
                if layout[0] <= row < sum(layout[:2]) and unary[row] and val != 0
            ]
            lower = [(row, value) for row, val, value in bounds if val < 0]
            upper = [(row, value) for row, val, value in bounds if val > 0]
            matches = [(lo, hi) for lo, lv in lower for hi, hv in upper if lv == hv]
            if matches:
                count += 1
                rows.extend(row for pair in matches for row in pair)
        if rows:
            unique = np.unique(rows)
            result[var["name"]] = dict(
                fixed_coordinates=count,
                dual_rows=len(unique),
                dual_norm=float(np.linalg.norm(z[unique])),
                fraction_total_dual_squared_norm=float(z[unique] @ z[unique] / (z @ z)),
            )
    return result


def precise_gap_identity(A, P, c, rhs, x, s, z, divisor):
    """40-digit arithmetic on the exact retained binary floats, not a new solve.

    NumPy longdouble is only float64 on this Apple silicon platform. Decimal
    makes the diagnostic independent of BLAS accumulation and platform dtype.
    """
    with localcontext() as ctx:
        ctx.prec = 40

        def decimal(v):
            return [Decimal.from_float(float(a)) for a in v]

        def matvec(matrix, v):
            csr = matrix.tocsr()
            out = []
            for start, stop in zip(csr.indptr[:-1], csr.indptr[1:], strict=True):
                out.append(
                    sum(
                        (
                            Decimal.from_float(float(a)) * v[i]
                            for a, i in zip(
                                csr.data[start:stop],
                                csr.indices[start:stop],
                                strict=True,
                            )
                        ),
                        Decimal(0),
                    )
                )
            return out

        def dot(a, b):
            return sum((u * v for u, v in zip(a, b, strict=True)), Decimal(0))

        x, s, z, c, rhs = [decimal(v) for v in (x, s, z, c, rhs)]
        Ax, Px, Atz = matvec(A, x), matvec(P, x), matvec(A.T, z)
        rp = [a + b - v for a, b, v in zip(Ax, s, rhs, strict=True)]
        rd = [a + b + v for a, b, v in zip(Px, c, Atz, strict=True)]
        multiplier = Decimal.from_float(float(divisor))
        terms = dict(
            x_dot_stationarity=float(multiplier * dot(x, rd)),
            complementarity=float(multiplier * dot(s, z)),
            minus_z_dot_primal_error=float(-multiplier * dot(z, rp)),
        )
        return terms, float(multiplier * (dot(x, Px) + dot(c, x) + dot(rhs, z)))


def analyze(directory, reconstructed=None, *, require_native_gap_agreement=True):
    completion = json.loads((directory / "completion.json").read_text())
    arm_path = directory / "arm.json.gz"
    if sha(arm_path) != completion["arm_sha256"]:
        raise ValueError("arm hash mismatch")
    with gzip.open(arm_path, "rt") as stream:
        r = json.load(stream)
    original_exception = r.get("exception")
    if reconstructed is not None:
        # Explicit offline interpretation of a failed native point; original
        # artifacts and unsuccessful solve classification remain unchanged.
        r.update(reconstructed)
    canonical_path = directory / "canonical.npz"
    if sha(canonical_path) != r["canonical_archive"]["sha256"]:
        raise ValueError("canonical hash mismatch")
    if r["context"]["packages"]["clarabel"] != "0.11.1":
        raise ValueError("normalization reconstruction is version-specific")
    with np.load(canonical_path, allow_pickle=False) as d:
        A, P = [
            sparse.csc_matrix(
                (d[k + "_data"], d[k + "_indices"], d[k + "_indptr"]),
                shape=d[k + "_shape"],
            )
            for k in ("A", "P")
        ]
        rhs, c = d["b"], d["c"]
    # CVXPY's retained P is the full symmetric quadratic matrix here.
    if inf((P - P.T).data) > 1e-15:
        raise ValueError("expected full symmetric P")
    native = r["native_solution"]
    x, s, z = [np.asarray(native[k]) for k in ("x", "s", "z")]
    layout = cone_layout(r["canonical_archive"]["cone_dimensions"], A.shape[0])
    variables = r["canonical_archive"]["variables"]
    covered = np.concatenate([indices(v) for v in variables])
    np.testing.assert_array_equal(np.sort(covered), np.arange(len(x)))
    epi, ti, bi, pairs = storage_epigraph(A, P, c, rhs, variables, layout)
    divisor = r["objective_divisor"]
    offset = r["canonical_evidence"]["canonical_offset"]
    rp = A @ x + s - rhs
    rd = P @ x + c + A.T @ z
    original_slack = rhs - A @ x
    tightened = x.copy()
    tightened[ti] = np.abs(x[bi])
    tight_slack = rhs - A @ tightened
    affected = np.unique(A[:, ti].nonzero()[0])
    untouched = np.ones(len(s), dtype=bool)
    untouched[affected] = False
    np.testing.assert_array_equal(original_slack[untouched], tight_slack[untouched])

    def objective(v):
        return float(divisor * (0.5 * v @ (P @ v) + c @ v + offset))

    cost_before, cost_after = objective(x), objective(tightened)
    np.testing.assert_allclose(
        cost_after, r["result"]["objective"], atol=1e-7, rtol=1e-10
    )
    groups = {}
    for var in variables:
        ii = indices(var)
        name = "storage_epigraph" if var == epi else var["name"]
        groups[name] = dict(
            size=len(ii),
            stationarity_inf_original=divisor * inf(rd[ii]),
            stationarity_l2_original=float(divisor * np.linalg.norm(rd[ii])),
            x_dot_stationarity_original=float(divisor * x[ii] @ rd[ii]),
        )
    norms = {
        name: float(np.linalg.norm(v))
        for name, v in (
            ("x", x),
            ("s", s),
            ("z", z),
            ("c", c),
            ("b", rhs),
            ("rp", rp),
            ("rd", rd),
        )
    }
    dp = max(1.0, norms["b"] + norms["x"] + norms["s"])
    dd = max(1.0, norms["c"] + norms["x"] + norms["z"])
    normalized_p, normalized_d = norms["rp"] / dp, norms["rd"] / dd
    # Returned postprocessed vectors can differ from internal stopping vectors.
    # Preserve discrepancies rather than forcing agreement with native summaries.
    gap = divisor * (native["obj_val"] - native["obj_val_dual"])
    float64_terms = dict(
        x_dot_stationarity=float(divisor * x @ rd),
        complementarity=float(divisor * s @ z),
        minus_z_dot_primal_error=float(-divisor * z @ rp),
    )
    # Cancellation in A.T @ z can contaminate x.T @ rd even when the small
    # residual is meaningful. Evaluate this algebraic identity in extended
    # precision; keep the ordinary-precision result visible as evidence.
    terms, vector_gap = precise_gap_identity(A, P, c, rhs, x, s, z, divisor)
    native_gap_agrees = bool(np.isclose(sum(terms.values()), gap, atol=1e-6, rtol=1e-5))
    if require_native_gap_agreement:
        np.testing.assert_allclose(sum(terms.values()), gap, atol=1e-6, rtol=1e-5)
    # A diagnostic may retain, rather than suppress, a failed comparison with
    # internal solver statistics. The original strict default is unchanged.
    np.testing.assert_allclose(sum(terms.values()), vector_gap, atol=1e-6, rtol=1e-5)
    dual_blocks = []
    for block in r["canonical_archive"]["constraints"]:
        section = z[block["start"] : block["stop"]]
        dual_blocks.append(
            dict(
                kind=block["kind"],
                start=block["start"],
                stop=block["stop"],
                l2=float(np.linalg.norm(section)),
                maximum=inf(section),
            )
        )
    return dict(
        directory=str(directory),
        input_hashes=dict(
            arm=sha(arm_path),
            canonical=sha(canonical_path),
            completion=sha(directory / "completion.json"),
        ),
        execution_context=r["context"],
        status=native["status"],
        original_exception=original_exception,
        offline_reconstructed=reconstructed is not None,
        objective_divisor=divisor,
        physical_audit_passed=r["audit"]["passed"],
        native_info=r["native_info"],
        objective=dict(
            canonical_before=cost_before,
            canonical_after=cost_after,
            original_expression=r["result"]["objective"],
            improvement=cost_before - cost_after,
            native_signed_gap=gap,
        ),
        primal_before=cone_errors(original_slack, layout),
        primal_after=cone_errors(tight_slack, layout),
        native_slack_cone=cone_errors(s, layout),
        dual_cone=cone_errors(z, layout, dual=True),
        physical_variables_unchanged=True,
        unaffected_rows_exactly_unchanged=True,
        groups=groups,
        norms=norms,
        dual_blocks=dual_blocks,
        fixed_box_multipliers=fixed_box_multipliers(A, rhs, z, variables, layout),
        normalization=dict(
            primal_denominator=dp,
            dual_denominator=dd,
            primal_reconstructed=normalized_p,
            dual_reconstructed=normalized_d,
            native_primal=native["r_prim"],
            native_dual=native["r_dual"],
            stationarity_inf_original=divisor * inf(rd),
        ),
        gap_identity_original_units=terms,
        float64_gap_identity_original_units=float64_terms,
        gap_reconstruction=dict(
            arithmetic="decimal_40_digits_from_exact_binary_floats",
            vector_gap=vector_gap,
            native_gap_agreement_passed=native_gap_agrees,
            native_gap_difference=sum(terms.values()) - gap,
            native_comparison_atol=1e-6,
            native_comparison_rtol=1e-5,
            require_native_gap_agreement=require_native_gap_agreement,
        ),
        storage=dict(
            count=len(ti),
            coefficient_original_min=float(divisor * min(c[ti])),
            coefficient_original_max=float(divisor * max(c[ti])),
            epigraph_excess_sum=float(np.sum(x[ti] - abs(x[bi]))),
            tightening_rows_min=float(np.min(tight_slack[affected])),
            multiplier_sum_original_min=float(divisor * min(z[pairs].sum(axis=1))),
            multiplier_sum_original_max=float(divisor * max(z[pairs].sum(axis=1))),
            complementarity_original=float(divisor * np.sum(s[pairs] * z[pairs])),
            multiplier_min_original=float(divisor * np.min(z[pairs])),
            stationarity_relative_to_coefficient_max=float(max(abs(rd[ti] / c[ti]))),
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    arms = [analyze(args.root / f"surplus-combined-divisor-{n}") for n in (1, 1000)]
    result = dict(
        schema=1,
        analysis_source_sha256=sha(Path(__file__)),
        no_new_solves=True,
        no_optimality_certificate=True,
        arms=arms,
    )
    encoded = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if args.output:
        with args.output.open("x") as stream:
            stream.write(encoded)
    else:
        print(encoded)
    if args.output:
        for arm in arms:
            print(
                json.dumps(
                    {
                        k: arm[k]
                        for k in (
                            "status",
                            "objective_divisor",
                            "objective",
                            "primal_after",
                            "normalization",
                            "gap_identity_original_units",
                            "storage",
                        )
                    }
                )
            )
        print("output_sha256", sha(args.output))


if __name__ == "__main__":
    main()
