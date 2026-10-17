"""Non-solving loop exclusion and retained SOCP non-tightness measurements.

Run with uv run --extra dev python -m experiments.tracy_battery_transfer_loop.analyze.
No optimizer is invoked. Decimal interval arithmetic encloses every voltage
box; point sampling is not used as a certificate. Old archives are read only.
"""

from decimal import Decimal, localcontext, ROUND_FLOOR, ROUND_CEILING
from functools import lru_cache
import json
from pathlib import Path

import cvxpy as cp
import numpy as np

from experiments.case118_tracy_2021 import e3_prepared
from experiments.tracy_battery_transfer import model as transfer
from experiments.tracy_battery_transfer_slice import model, run


class I:  # noqa: E742 -- conventional interval-arithmetic notation.
    """Closed intervals with directed, 36-digit Decimal arithmetic."""

    def __init__(self, lo, hi=None):
        def dec(x):
            return Decimal.from_float(x) if isinstance(x, float) else Decimal(x)

        self.lo, self.hi = dec(lo), dec(lo if hi is None else hi)
        assert self.lo <= self.hi

    def __add__(self, other):
        other = other if isinstance(other, I) else I(other)
        with localcontext() as c:
            c.prec, c.rounding = 36, ROUND_FLOOR
            lo = self.lo + other.lo
            c.rounding = ROUND_CEILING
            hi = self.hi + other.hi
        return I(lo, hi)

    __radd__ = __add__

    def __neg__(self):
        # Decimal unary minus otherwise uses the ambient rounding context.
        return I(self.hi.copy_negate(), self.lo.copy_negate())

    def __sub__(self, other):
        return self + -(other if isinstance(other, I) else I(other))

    def __rsub__(self, other):
        return I(other) + -self

    def __mul__(self, other):
        other = other if isinstance(other, I) else I(other)
        pairs = [(a, b) for a in (self.lo, self.hi) for b in (other.lo, other.hi)]
        with localcontext() as c:
            c.prec, c.rounding = 36, ROUND_FLOOR
            lo = min(a * b for a, b in pairs)
            c.rounding = ROUND_CEILING
            hi = max(a * b for a, b in pairs)
        return I(lo, hi)

    __rmul__ = __mul__

    def __truediv__(self, other):
        other = other if isinstance(other, I) else I(other)
        assert not other.lo <= 0 <= other.hi
        with localcontext() as c:
            c.prec, c.rounding = 36, ROUND_FLOOR
            lo = 1 / other.hi
            c.rounding = ROUND_CEILING
            hi = 1 / other.lo
        return self * I(lo, hi)

    def __pow__(self, n):
        assert isinstance(n, int) and n >= 0
        out = I(1)
        for _ in range(n):
            out = out * self
        return out

    def sqrt(self):
        assert self.lo >= 0
        with localcontext() as c:
            c.prec = 36
            # Decimal.sqrt is correctly rounded to nearest: widen one ulp.
            lo = self.lo.sqrt().next_minus() if self.lo else Decimal(0)
            hi = self.hi.sqrt().next_plus()
        return I(lo, hi)


def atan_small(x):
    assert max(x.lo.copy_abs(), x.hi.copy_abs()) <= Decimal("0.5")
    total, power, square = I(0), x, x * x
    for k in range(70):
        total = total + power / (2 * k + 1) * (1 if k % 2 == 0 else -1)
        power = power * square
    # Taylor remainder |x|^141/141, enclosed without assuming alternation.
    error = I(max(x.lo.copy_abs(), x.hi.copy_abs())) ** 141 / 141
    return total + I(error.hi.copy_negate(), error.hi)


PI = 16 * atan_small(I(1) / 5) - 4 * atan_small(I(1) / 239)


def atan(x):
    def endpoint(v):
        if v < 0:
            return -endpoint(v.copy_negate())
        if v <= Decimal("0.5"):
            return atan_small(I(v))
        # Argument reduction keeps the Taylor argument below 1/2.
        if v <= 2:
            return PI / 4 + atan_small((I(v) - 1) / (I(v) + 1))
        return PI / 2 - atan(I(1) / I(v))

    return I(endpoint(x.lo).lo, endpoint(x.hi).hi)


def asin_lower(q):
    if q <= -1:
        return (-PI / 2).lo
    assert q < 1
    x = I(q)
    return atan(x / (1 - x * x).sqrt()).lo


def acos_upper(q):
    assert q >= 0
    if q >= 1:
        return Decimal(0)
    x = I(q)
    return atan((1 - x * x).sqrt() / x).hi


def forbid_solves(*args, **kwargs):
    raise RuntimeError("This analysis must not invoke an optimizer")


def prove_loop(kwargs, pins, max_boxes=500000):
    assert kwargs["formulation"] == "ac" and kwargs["T"] == 1
    assert kwargs["options"].enforce_branch_limits
    case, base = kwargs["case"], I(float(kwargs["case"]["baseMVA"]))
    buses = {int(row[0]): row for row in case["bus"]}
    edges = {}
    for key, pair in {
        "a": (70, 71),
        "b": (71, 72),
        "c": (24, 72),
        "d": (24, 70),
        "e": (71, 73),
    }.items():
        rows = [r for r in case["branch"] if tuple(r[:2]) == pair and r[10] > 0]
        assert len(rows) == 1
        row = rows[0]
        assert row[8] in (0, 1) and row[9] == 0
        r, x, ch, rating = (I(float(row[k])) for k in (2, 3, 4, 5))
        den = r * r + x * x
        g, b = r / den, x / den
        cap = g * g + b * (b - ch / 2)
        edges[key] = dict(
            r=r,
            x=x,
            ch=ch / 2,
            rating=rating,
            g=g,
            b=b,
            y2=g * g + b * b,
            a2=g * g + (b - ch / 2) * (b - ch / 2),
            cross=(cap * cap + (g * ch / 2) * (g * ch / 2)).sqrt(),
            alpha=-atan((g * ch / 2) / cap),
        )
    assert kwargs.get("hvdc") is None
    for bus, neighbors in [(71, {70, 72, 73}), (72, {24, 71}), (73, {71})]:
        actual = {
            int(r[1] if r[0] == bus else r[0])
            for r in case["branch"]
            if r[10] > 0 and bus in r[:2]
        }
        assert actual == neighbors and buses[bus][4] == 0
    assert all(g.p_min_mw >= 0 for g in kwargs["generators"] if g.bus in (71, 72, 73))
    assert all(g.p_max_mw == 0 for g in kwargs["generators"] if g.bus in (72, 73))
    assert not any(s.bus == 73 for s in kwargs["storage"])
    assert all(
        load.q_load_mvar in (None, 0) for load in kwargs["loads"] if load.bus == 73
    )
    assert float(kwargs["df_load_q"]["load_bus_73"].iloc[0]) == 0
    load72, load73 = (
        I(float(kwargs["df_load_p"][f"load_bus_{b}"].iloc[0])) for b in (72, 73)
    )
    available73 = sum(
        float(kwargs["df_nd"][u.device_id].iloc[0])
        for u in kwargs["nondispatchable"]
        if u.bus == 73
    )
    assert 0 <= available73 <= float(load73.lo)
    b71, b72 = (
        I(float(sum(pins[j] for j, s in enumerate(kwargs["storage"]) if s.bus == b)))
        for b in (71, 72)
    )
    vmin, vmax = (
        I(float(min(buses[b][12] for b in (24, 70, 71, 72, 73)))),
        I(float(max(buses[b][11] for b in (24, 70, 71, 72, 73)))),
    )
    q73 = sum(
        (
            I(float(max(abs(g.q_min_mvar), abs(g.q_max_mvar))))
            for g in kwargs["generators"]
            if g.bus == 73
        ),
        I(0),
    )
    q73 = q73 + sum(
        (
            I(float(u.apparent_power_rating))
            for u in kwargs["nondispatchable"]
            if u.bus == 73
        ),
        I(0),
    )
    e = edges["e"]
    loss73 = (
        base
        * e["r"]
        * ((load73 * load73 + q73 * q73).sqrt() / (base * vmin) + e["ch"] * vmax) ** 2
    )
    e = edges["b"]
    loss12 = base * e["r"] * (e["rating"] / (base * vmin) + e["ch"] * vmax) ** 2
    p12 = b71 - load73 - loss73 - edges["a"]["rating"]
    p24 = p12 - loss12 + b72 - load72
    # Principal edge angles all lie in a small arc from |Vi-Vj| <= |z| Imax.
    # Sum of four such arcs is below pi, so cycle closure has no 2pi ambiguity.
    arc = {}
    for key in ("a", "b", "c", "d"):
        e = edges[key]
        q = (
            (e["r"] * e["r"] + e["x"] * e["x"]).sqrt()
            * (e["rating"] / (base * vmin) + e["ch"] * vmax)
            / (2 * vmin)
        )
        arc[key] = I(2) * atan(q / (1 - q * q).sqrt())
    assert sum((v for v in arc.values()), I(0)).hi < PI.lo
    for key in ("b", "c"):
        center = atan(edges[key]["g"] / edges[key]["b"])
        assert arc[key].hi + center.hi < (PI / 2).lo

    @lru_cache(None)
    def high_angle(key, ubox, vbox):
        u, v = I(*ubox), I(*vbox)
        e = edges[key]

        def k(u, v):
            # Algebraically identical to the terminal circle, but retains
            # the cancellation near equal voltage magnitudes. This improves
            # interval enclosures without changing a constraint or gate.
            diff = u - v
            sq = diff * diff
            if diff.lo <= 0 <= diff.hi:
                sq = I(0, sq.hi)
            cross = e["cross"]
            return (
                1
                + sq / (2 * u * v)
                + (e["a2"] - cross) * u / (2 * cross * v)
                + (e["y2"] - cross) * v / (2 * cross * u)
                - (e["rating"] / base) ** 2 / (2 * cross * u**3 * v)
            )

        k1, k2 = k(u, v), k(v, u)
        if k1.lo > 1 or k2.lo > 1:
            return None
        assert k1.lo > 0 and k2.lo > 0
        return min(
            (e["alpha"] + I(acos_upper(k1.lo))).hi,
            (-e["alpha"] + I(acos_upper(k2.lo))).hi,
        )

    @lru_cache(None)
    def low_angle(key, ubox, vbox):
        u, v = I(*ubox), I(*vbox)
        e = edges[key]
        power = p12 if key == "b" else p24
        q = (power / base - e["g"] * u * u) / (e["y2"].sqrt() * u * v)
        if q.lo >= 1:
            return None
        return (atan(e["g"] / e["b"]) + I(asin_lower(q.lo))).lo

    initial = tuple(
        (
            Decimal.from_float(float(buses[b][12])),
            Decimal.from_float(float(buses[b][11])),
        )
        for b in (70, 71, 72, 24)
    )
    stack = [initial]
    n = excluded = thermal = 0
    minimum = None
    max_depth = 0
    leaves = []
    while stack and n < max_boxes:
        box = stack.pop()
        n += 1
        u70, u71, u72, u24 = box
        a = high_angle("a", u71, u70)
        d = high_angle("d", u70, u24)
        b = low_angle("b", u71, u72)
        c = low_angle("c", u72, u24)
        if any(v is None for v in (a, b, c, d)):
            thermal += 1
            continue
        margin = (I(b) + I(c) - I(a) - I(d)).lo
        if margin > 0:
            excluded += 1
            minimum = margin if minimum is None else min(minimum, margin)
            leaves.append(
                dict(
                    voltage_boxes=[[str(v) for v in pair] for pair in box],
                    lower_angles_radians=[str(b), str(c)],
                    upper_angles_radians=[str(a), str(d)],
                    margin_radians=str(margin),
                )
            )
            continue
        j = max(range(4), key=lambda j: box[j][1] - box[j][0])
        if box[j][0] == box[j][1]:
            stack.append(box)
            break
        with localcontext() as ctx:
            ctx.prec = 60
            midpoint = (box[j][0] + box[j][1]) / 2
        assert box[j][0] < midpoint < box[j][1]
        left, right = list(box), list(box)
        left[j] = (box[j][0], midpoint)
        right[j] = (midpoint, box[j][1])
        stack.extend((tuple(left), tuple(right)))
        max_depth = max(max_depth, len(stack))

    def dec(i):
        return [str(i.lo), str(i.hi)]

    return dict(
        proved_exclusion=not stack,
        tested_boxes=n,
        angle_excluded_leaves=excluded,
        other_excluded_leaves=thermal,
        unresolved_boxes=len(stack),
        minimum_certified_leaf_margin_radians=str(minimum),
        p71_to72_lower_mw=dec(p12),
        p72_to24_lower_mw=dec(p24),
        loss71_73_upper_mw=dec(loss73),
        loss71_72_upper_mw=dec(loss12),
        voltage_range=dec(I(vmin.lo, vmax.hi)),
        b71_mw=dec(b71),
        b72_mw=dec(b72),
        load72_mw=dec(load72),
        load73_mw=dec(load73),
        decimal_precision=36,
        atan_terms=70,
        max_stack=max_depth,
        certificate_leaves=leaves,
    )


def slack_metrics(point, start=None):
    w = np.asarray(point["w"])
    z = np.asarray(point["W_re"]) + 1j * np.asarray(point["W_im"])
    pairs = np.asarray(point["voltage_product_pairs"])
    i, j = pairs.T
    if w.ndim == 1:
        w = w[None, :]
        z = z[None, :]
    prod = w[:, i] * w[:, j]
    gap = prod - abs(z) ** 2
    rel = gap / prod
    h, e = np.unravel_index(np.argmax(rel), rel.shape)
    return (
        dict(
            max_relative=float(rel[h, e]),
            max_gap_pu4=float(gap.max()),
            mean_relative=float(rel.mean()),
            p95_relative=float(np.percentile(rel, 95)),
            non_tight_count=int(np.sum(rel > 1e-5)),
            edge_hour_count=int(rel.size),
            max_edge_external=(pairs[e] + 1).tolist(),
            max_offset=int(h),
            max_hour=None if start is None else start + int(h),
        ),
        gap,
        rel,
        z,
        pairs,
    )


def main():
    cp.Problem.solve = forbid_solves
    root = model.HERE / "results" / "slice_002"
    binding = run.verify(root)
    data = transfer.source_inventory()
    prepared = e3_prepared.verified_inputs()
    output = dict(
        evidence_kind="non-solving necessary-condition certificate and retained-point measurements",
        source_commit=binding["context"]["commit"],
        analysis_sha256=transfer.sha(Path(__file__)),
        slice_binding_sha256=transfer.sha(root / "binding.json"),
        loop={},
        socp_comparison=[],
    )
    for n in (2, 3, 4):
        build, kwargs, powers, _, _, row = model.construct(n, data, prepared)
        assert row["donor"] == binding["rows"][n - 1]["donor"]
        output["loop"][model.FORMS[n - 1]] = prove_loop(kwargs, powers[0])
        output["loop"][model.FORMS[n - 1]]["source"] = row["donor"]
        print(
            "LOOP", model.FORMS[n - 1], output["loop"][model.FORMS[n - 1]], flush=True
        )
    source = model.source_case(data)["socp"]
    for label, case in data["cases"].items():
        record = case["socp"]
        path = transfer.source_path(data, record)
        saved = transfer.read(path)
        assert saved["classification"] == record["classification"]
        stats, gap, rel, z, pairs = slack_metrics(
            saved["result"], record["arm"]["start"]
        )
        stats.update(
            label=label,
            source=transfer.source_reference(data, record),
            classification=saved["classification"],
            relaxation_feasible=saved["relaxation_audit"]["feasible"],
        )
        output["socp_comparison"].append(stats)
        if record == source:
            h = 13
            stats_h, *_ = slack_metrics(
                {
                    k: np.asarray(v)[h : h + 1] if k in ("w", "W_re", "W_im") else v
                    for k, v in saved["result"].items()
                },
                2452,
            )
            loop = []
            angles = []
            for a, b in ((71, 72), (72, 24), (24, 70), (70, 71), (71, 73)):
                pair = sorted((a - 1, b - 1))
                e = int(np.flatnonzero((pairs == pair).all(axis=1))[0])
                sgn = 1 if a < b else -1
                angle = float(sgn * np.angle(z[h, e]))
                angles.append(angle)
                loop.append(
                    dict(
                        edge=[a, b],
                        gap_pu4=float(gap[h, e]),
                        relative=float(rel[h, e]),
                        angle_deg=float(np.rad2deg(angle)),
                    )
                )
            output["target_socp"] = dict(
                hour=2452,
                hour_metrics=stats_h,
                loop_edges=loop,
                loop_closure_deg=float(np.rad2deg(sum(angles[:4]))),
            )
    # Historical optimized points, not rank-one lifts of accepted AC points.
    output["m11_comparison"] = []
    for path in sorted(Path("experiments/m11_socp/results/e2").glob("*.json")):
        saved = transfer.read(path)
        if "socp_result" not in saved:
            continue
        assert saved["accepted"]
        stats, *_ = slack_metrics(saved["socp_result"])
        stats.update(
            label="E2 v1 " + saved["name"], path=str(path), sha256=transfer.sha(path)
        )
        output["m11_comparison"].append(stats)
    for path in sorted(
        Path("experiments/m11_socp/results/e2_v2_001").glob("call-*/result.json.gz")
    ):
        saved = transfer.read(path)
        assert saved["classification"] == "accepted"
        stats, *_ = slack_metrics(saved["records"]["socp"]["result"])
        stats.update(
            label="E2 v2 " + saved["row"]["name"],
            path=str(path),
            sha256=transfer.sha(path),
        )
        output["m11_comparison"].append(stats)
    print("EVIDENCE_JSON")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
