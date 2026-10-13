"""Topology-discovered, report-only necessary AC loop conditions.

No optimizer, candidate repair, or acceptance mutation. A bounded search can
prove exclusion; every other result is unresolved, never a feasibility claim.
Uses the corrected directed Decimal primitives of the historical checker,
but neither its named topology nor its proof routine.
"""

from collections import Counter
from decimal import Decimal, localcontext, ROUND_CEILING, ROUND_FLOOR
from functools import lru_cache

import numpy as np

from experiments.tracy_battery_transfer_loop.analyze import (
    I,
    PI,
    atan,
    asin_lower,
    acos_upper,
)
from experiments.tracy_socp_transfer_preflight.preflight import short_cycles


def square(x):
    result = x * x
    return I(0, result.hi) if x.lo <= 0 <= x.hi else result


def endpoints(x):
    return [str(x.lo), str(x.hi)]


def magnitude(x):
    return max(x.lo.copy_abs(), x.hi.copy_abs())


def frame_value(kwargs, key, unit, offset, fallback):
    frame = kwargs.get(key)
    return float(fallback if frame is None else frame[unit.device_id].iloc[offset])


def injections(kwargs, pins, offset):
    """Engineering-unit boxes, enlarged rather than guessed for omitted coupling.

    Reactive circles, voltage setpoints, and temporal constraints are relaxed
    only in this necessary-condition calculation, never in a physical model.
    """
    if kwargs.get("hvdc"):
        raise ValueError("HVDC injection bounds are not supported")
    if kwargs.get("generators") is None or kwargs.get("loads") is None:
        raise ValueError("explicit generator and load inputs are required")
    ids = [int(b[0]) for b in kwargs["case"]["bus"]]
    values = {b: [I(0), I(0)] for b in ids}

    def add(bus, p, q):
        values[bus][0] = values[bus][0] + p
        values[bus][1] = values[bus][1] + q

    for g in kwargs["generators"]:
        if g.status:
            add(
                g.bus,
                I(float(g.p_min_mw), float(g.p_max_mw)),
                I(float(g.q_min_mvar), float(g.q_max_mvar)),
            )
    storage = kwargs.get("storage") or []
    if len(pins) != len(storage) or not np.isfinite(pins).all():
        raise ValueError("finite hourly pins aligned to the storage list required")
    for unit, pin in zip(storage, pins):
        rating = I(float(unit.apparent_power_rating))
        add(unit.bus, I(float(pin)), I(rating.hi.copy_negate(), rating.hi))
    for unit in kwargs.get("nondispatchable") or []:
        available = frame_value(kwargs, "df_nd", unit, offset, unit.p_available)
        rating = float(unit.apparent_power_rating)
        if not 0 <= available or not np.isfinite([available, rating]).all():
            raise ValueError("invalid nondispatchable bounds")
        add(unit.bus, I(0, min(available, rating)), I(-rating, rating))
    for load in kwargs["loads"]:
        p = I(frame_value(kwargs, "df_load_p", load, offset, load.p_load_mw))
        q = I(frame_value(kwargs, "df_load_q", load, offset, load.q_load_mvar or 0))
        if load.shedding_cost_per_mwh is not None and p.lo > 0:
            served = p * (1 - I(float(load.max_shed_fraction)))
            p = I(served.lo, p.hi)
            # Includes proportional Q relief and either sign of nominal Q.
            q = I(min(Decimal(0), q.lo), max(Decimal(0), q.hi))
        add(load.bus, -p, -q)
    return values


def winding_gap(angle):
    """Positive separation from every possible 2*pi winding, or None.

    The quotient is an enclosure, so integer ceil/floor cannot lose a winding
    because of a rounded floating-point pi or ambient Decimal precision.
    """
    quotient = angle / (2 * PI)
    lower = quotient.lo.to_integral_value(rounding=ROUND_CEILING)
    upper = quotient.hi.to_integral_value(rounding=ROUND_FLOOR)
    if lower <= upper:
        return None
    previous = I(upper) * (2 * PI)
    following = I(lower) * (2 * PI)
    gap = min((angle - previous).lo, (following - angle).lo)
    return gap if gap > 0 else None


class LoopScanner:
    """Discover all simple cycles of length 3..max_length, bounded explicitly.

    Certificates currently support passive, rated, unit-tap, zero-shift
    branches with small thermal angle arcs. Unsupported loops remain visible.
    Every contiguous cycle arc is a candidate cut, with and without components
    attached to just one cycle vertex. Bus numbers never select a cut or loop.
    """

    def __init__(self, kwargs, *, max_length=4, max_boxes=64):
        if type(max_length) is not int or not 3 <= max_length <= 6:
            raise ValueError("cycle length must be an integer in [3, 6]")
        if type(max_boxes) is not int or max_boxes < 1:
            raise ValueError("positive integer box budget required")
        self.kwargs, self.max_boxes, self.max_length = kwargs, max_boxes, max_length
        self.base = I(float(kwargs["case"]["baseMVA"]))
        self.buses = {int(b[0]): b for b in kwargs["case"]["bus"]}
        self.voltage = {b: I(float(r[12]), float(r[11])) for b, r in self.buses.items()}
        self.rows = [r for r in kwargs["case"]["branch"] if r[10] > 0]
        self.pairs, self.incident = {}, {b: [] for b in self.buses}
        self.geometry, self.reasons = {}, {}
        self.global_reason = None
        if kwargs["formulation"] != "ac" or not kwargs["options"].enforce_branch_limits:
            self.global_reason = "requires AC and enforced two-terminal ratings"
        if kwargs.get("hvdc"):
            self.global_reason = "HVDC injection bounds are not supported"
        if kwargs.get("generators") is None or kwargs.get("loads") is None:
            self.global_reason = "explicit generator and load inputs are required"
        if self.base.lo <= 0 or any(v.lo <= 0 for v in self.voltage.values()):
            self.global_reason = "positive base and voltage bounds required"
        for index, row in enumerate(self.rows):
            a, b = map(int, row[:2])
            self.pairs.setdefault(tuple(sorted((a, b))), []).append(index)
            self.incident[a].append(index)
            self.incident[b].append(index)
            try:
                self.geometry[index] = self.make_geometry(row)
            except ValueError as exc:
                self.reasons[index] = str(exc)
        self.cycles = short_cycles(list(self.pairs), max_length)
        self.cuts = {cycle: self.make_cuts(cycle) for cycle in self.cycles}

    def make_geometry(self, row):
        if not np.isfinite(row[[2, 3, 4, 5, 8, 9]]).all():
            raise ValueError("nonfinite branch geometry")
        if row[5] <= 0:
            raise ValueError("missing positive thermal rating")
        if row[8] not in (0, 1) or row[9] != 0:
            raise ValueError("transformer tap or phase shift unsupported")
        if row[2] < 0 or row[3] <= 0 or row[4] < 0:
            raise ValueError("nonpassive or unsupported charging geometry")
        r, x, c, rating = (I(float(row[k])) for k in (2, 3, 4, 5))
        c = c / 2
        den = square(r) + square(x)
        g, b = r / den, x / den
        if c.hi > (b / 2).lo:
            raise ValueError("large charging geometry unsupported")
        cross_real = square(g) + b * (b - c)
        cross = (square(cross_real) + square(g * c)).sqrt()
        return dict(
            r=r,
            x=x,
            c=c,
            rating=rating,
            g=g,
            b=b,
            y2=square(g) + square(b),
            a2=square(g) + square(b - c),
            cross=cross,
            alpha=-atan(g * c / cross_real),
            center=atan(g / b),
        )

    def make_cuts(self, cycle):
        vertices = set(cycle)
        adjacency = {b: set() for b in self.buses}
        for a, b in self.pairs:
            adjacency[a].add(b)
            adjacency[b].add(a)
        remaining = set(self.buses) - vertices
        pendants = {b: set() for b in cycle}
        while remaining:
            todo, component = [min(remaining)], set()
            while todo:
                node = todo.pop()
                if node in component:
                    continue
                component.add(node)
                todo.extend((adjacency[node] - vertices) - component)
            remaining -= component
            contacts = set().union(*(adjacency[b] & vertices for b in component))
            if len(contacts) == 1:
                pendants[next(iter(contacts))].update(component)
        sets = set()
        for start in range(len(cycle)):
            for size in range(1, len(cycle)):
                arc = frozenset(cycle[(start + k) % len(cycle)] for k in range(size))
                sets.add(arc)
                sets.add(arc | set().union(*(pendants[b] for b in arc)))
        cuts = []
        for nodes in sorted(sets, key=lambda s: (len(s), sorted(s))):
            internal, boundary = [], []
            for index, row in enumerate(self.rows):
                a, b = map(int, row[:2])
                if a in nodes and b in nodes:
                    internal.append(index)
                elif (a in nodes) != (b in nodes):
                    boundary.append(index)
            # All terms needed for this cut must have justified finite bounds.
            if any(i not in self.geometry for i in internal) or any(
                not np.isfinite(self.rows[i][5]) or self.rows[i][5] <= 0
                for i in boundary
            ):
                continue
            cuts.append((nodes, internal, boundary))
        return cuts

    def current_cap(self, index, values=None):
        row, e = self.rows[index], self.geometry[index]
        caps = []
        for bus in map(int, row[:2]):
            v = self.voltage[bus]
            terminal = e["rating"]
            if values is not None and len(self.incident[bus]) == 1:
                p, q = values[bus]
                busrow = self.buses[bus]
                p = p - I(float(busrow[4])) * square(v)
                q = q + I(float(busrow[5])) * square(v)
                leaf_cap = (square(I(magnitude(p))) + square(I(magnitude(q)))).sqrt()
                terminal = I(min(terminal.hi, leaf_cap.hi))
            caps.append((terminal / (self.base * I(v.lo)) + e["c"] * I(v.hi)).hi)
        return I(min(caps))

    @lru_cache(None)
    def arc(self, index):
        e = self.geometry[index]
        a, b = map(int, self.rows[index][:2])
        q = (square(e["r"]) + square(e["x"])).sqrt() * self.current_cap(index)
        q = q / (2 * (I(self.voltage[a].lo) * I(self.voltage[b].lo)).sqrt())
        if q.hi >= 1:
            raise ValueError("large thermal angle arc unsupported")
        angle = 2 * atan(q / (1 - square(q)).sqrt())
        if angle.hi + e["center"].hi >= (PI / 2).lo:
            raise ValueError("nonmonotone real-power angle arc unsupported")
        return angle.hi

    def export_bounds(self, cycle, values, losses):
        bounds = {}
        cycle_pairs = list(zip(cycle, (*cycle[1:], cycle[0])))
        for a, b in cycle_pairs:
            for start, end in ((a, b), (b, a)):
                best = None
                index = self.pairs[tuple(sorted((a, b)))][0]
                for nodes, internal, boundary in self.cuts[cycle]:
                    if start not in nodes or end in nodes:
                        continue
                    injection = sum((I(values[n][0].lo) for n in sorted(nodes)), I(0))
                    shunts = sum(
                        (
                            I((I(float(self.buses[n][4])) * square(self.voltage[n])).hi)
                            for n in sorted(nodes)
                        ),
                        I(0),
                    )
                    loss = sum((losses[i] for i in internal), I(0))
                    other = sum(
                        (I(float(self.rows[i][5])) for i in boundary if i != index),
                        I(0),
                    )
                    required = injection - shunts - loss - other
                    if best is None or required.lo > best[0]:
                        best = (
                            required.lo,
                            sorted(nodes),
                            endpoints(injection),
                            endpoints(shunts),
                            endpoints(loss),
                            endpoints(other),
                        )
                bounds[start, end] = best
        return bounds

    @lru_cache(None)
    def thermal_upper(self, index, ubox, vbox):
        u, v, e = I(*ubox), I(*vbox), self.geometry[index]

        def k(u, v):
            cross = e["cross"]
            return (
                1
                + square(u - v) / (2 * u * v)
                + (e["a2"] - cross) * u / (2 * cross * v)
                + (e["y2"] - cross) * v / (2 * cross * u)
                - square(e["rating"] / self.base) / (2 * cross * u**3 * v)
            )

        k1, k2 = k(u, v), k(v, u)
        if k1.lo > 1 or k2.lo > 1:
            return None

        def cap(q):
            return PI.hi if q <= 0 else acos_upper(q)

        return min(
            (e["alpha"] + I(cap(k1.lo))).hi,
            (-e["alpha"] + I(cap(k2.lo))).hi,
            self.arc(index),
        )

    def power_lower(self, index, ubox, vbox, bound):
        if bound is None:
            return self.arc(index).copy_negate()
        u, v, e = I(*ubox), I(*vbox), self.geometry[index]
        q = (I(bound[0]) / self.base - e["g"] * square(u)) / (e["y2"].sqrt() * u * v)
        if q.lo >= 1:
            return None
        return (e["center"] + I(asin_lower(q.lo))).lo

    def prove(self, cycle, bounds):
        initial = tuple((self.voltage[n].lo, self.voltage[n].hi) for n in cycle)
        stack, leaves, visits, nonclosure = [initial], [], 0, 0
        pairs = list(zip(cycle, (*cycle[1:], cycle[0])))
        while stack and visits < self.max_boxes:
            box = stack.pop()
            visits += 1
            angle, impossible = I(0), False
            edge_angles = []
            for j, (a, b) in enumerate(pairs):
                index = self.pairs[tuple(sorted((a, b)))][0]
                u, v = box[j], box[(j + 1) % len(cycle)]
                high = self.thermal_upper(index, u, v)
                reverse_high = self.thermal_upper(index, v, u)
                low_power = self.power_lower(index, u, v, bounds[a, b])
                reverse_power = self.power_lower(index, v, u, bounds[b, a])
                if any(
                    x is None for x in (high, reverse_high, low_power, reverse_power)
                ):
                    impossible = True
                    break
                low = max(reverse_high.copy_negate(), low_power)
                high = min(high, reverse_power.copy_negate())
                if low > high:
                    impossible = True
                    break
                interval = I(low, high)
                edge_angles.append(endpoints(interval))
                angle = angle + interval
            gap = None if impossible else winding_gap(angle)
            if impossible or gap is not None:
                nonclosure += int(not impossible)
                leaves.append(
                    dict(
                        voltage_boxes=[[str(x) for x in pair] for pair in box],
                        reason="empty_edge_angle" if impossible else "cycle_nonclosure",
                        edge_angle_intervals_radians=edge_angles,
                        closure_interval_radians=None
                        if impossible
                        else endpoints(angle),
                        winding_gap_radians=None if gap is None else str(gap),
                    )
                )
                continue
            j = max(range(len(box)), key=lambda k: (I(box[k][1]) - I(box[k][0])).hi)
            if box[j][0] == box[j][1]:
                stack.append(box)
                break
            with localcontext() as ctx:
                ctx.prec = 60
                mid = (box[j][0] + box[j][1]) / 2
            if not box[j][0] < mid < box[j][1]:
                stack.append(box)
                break
            left, right = list(box), list(box)
            left[j], right[j] = (box[j][0], mid), (mid, box[j][1])
            stack.extend((tuple(left), tuple(right)))
        result = dict(
            status="certified_incompatible" if not stack else "unresolved_budget",
            tested_boxes=visits,
            unresolved_boxes=len(stack),
            excluded_leaves=len(leaves),
            nonclosure_leaves=nonclosure,
        )
        if not stack:
            result.update(
                voltage_bus_order=list(cycle),
                certificate_leaves=leaves,
                necessary_exports=[
                    dict(
                        edge=list(edge),
                        lower_mw=str(bound[0]),
                        cut_buses=bound[1],
                        injection_lower_enclosure=bound[2],
                        shunt_upper_enclosure=bound[3],
                        loss_upper_enclosure=bound[4],
                        other_boundary_rating_enclosure=bound[5],
                    )
                    for edge, bound in bounds.items()
                    if bound is not None
                ],
            )
        return result

    def scan_hour(self, pins, offset=0):
        records = []
        if self.global_reason:
            return dict(
                status="unresolved",
                loops=[
                    dict(cycle=list(c), status="unsupported", reason=self.global_reason)
                    for c in self.cycles
                ],
                loop_counts={"unsupported": len(self.cycles)},
            )
        values = injections(self.kwargs, pins, offset)
        losses = {
            i: I((self.base * e["r"] * square(self.current_cap(i, values))).hi)
            for i, e in self.geometry.items()
        }
        for cycle in self.cycles:
            record = dict(cycle=list(cycle))
            try:
                for pair in zip(cycle, (*cycle[1:], cycle[0])):
                    indices = self.pairs[tuple(sorted(pair))]
                    if len(indices) != 1:
                        raise ValueError("parallel cycle edges unsupported")
                    if indices[0] not in self.geometry:
                        raise ValueError(self.reasons[indices[0]])
                    self.arc(indices[0])
                bounds = self.export_bounds(cycle, values, losses)
                if not any(b is not None and b[0] > 0 for b in bounds.values()):
                    record.update(status="unresolved_no_export_trigger")
                else:
                    record.update(self.prove(cycle, bounds))
            except ValueError as exc:
                record.update(status="unsupported", reason=str(exc))
            records.append(record)
        return dict(
            status="certified_incompatible"
            if any(r["status"] == "certified_incompatible" for r in records)
            else "unresolved",
            loops=records,
            loop_counts=dict(Counter(r["status"] for r in records)),
        )

    def scan_schedule(self, schedule, first_hour=0):
        schedule = np.asarray(schedule, float)
        if schedule.shape != (self.kwargs["T"], len(self.kwargs.get("storage") or [])):
            raise ValueError("pins must have shape (T, number of storage devices)")
        hours = [
            dict(hour=first_hour + h, **self.scan_hour(pins, h))
            for h, pins in enumerate(schedule)
        ]
        legend = dict(
            certified_incompatible="C",
            unresolved_budget="B",
            unresolved_no_export_trigger="N",
            unsupported="U",
        )
        # Preserve every loop's disposition without repeating static topology
        # and unsupported reasons hundreds of times in the evidence bundle.
        unsupported = {}
        for hour in hours:
            hour["loop_status_codes"] = "".join(
                legend[r["status"]] for r in hour["loops"]
            )
            for j, record in enumerate(hour["loops"]):
                if record["status"] == "unsupported":
                    unsupported[j] = record["reason"]
            hour["loops"] = [
                dict(cycle_index=j, **record)
                for j, record in enumerate(hour["loops"])
                if record["status"] in ("certified_incompatible", "unresolved_budget")
            ]
        return dict(
            verdict="certified_incompatible"
            if any(h["status"] == "certified_incompatible" for h in hours)
            else "unresolved",
            max_cycle_length=self.max_length,
            discovered_cycles=len(self.cycles),
            cycle_catalog=[list(c) for c in self.cycles],
            unsupported_cycles=[
                dict(cycle_index=j, reason=reason)
                for j, reason in sorted(unsupported.items())
            ],
            loop_status_legend=legend,
            max_boxes_per_triggered_loop_hour=self.max_boxes,
            hours=hours,
        )
