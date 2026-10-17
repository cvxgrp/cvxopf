"""Report-only parallel-branch and fixed-transformer extension of v1.

Keep v1 source/evidence immutable. Work in transformer-internal coordinates;
intersect every physical branch's angle restrictions modulo 2*pi. Parallel
ratings are not replaced by an equivalent line or a guessed flow allocation.
"""

from collections import Counter
from decimal import Decimal, localcontext, ROUND_CEILING, ROUND_FLOOR
from functools import lru_cache

import numpy as np

from experiments.tracy_socp_transfer_preflight import automatic_loops as v1

I, PI = v1.I, v1.PI  # noqa: E741 -- conventional interval-arithmetic notation.


def intersect_modulo(anchor, other):
    """Enclose the circular intersection in anchor's unwrapped coordinates.

    A hull across multiple surviving lifts only enlarges the necessary set.
    Never intersect two different principal representatives as real numbers
    without first accounting for integer windings.
    """
    possible = (anchor - other) / (2 * PI)
    lower = int(possible.lo.to_integral_value(rounding=ROUND_CEILING))
    upper = int(possible.hi.to_integral_value(rounding=ROUND_FLOOR))
    if upper - lower > 4:
        raise ValueError("phase-shift enclosure too wide for modular intersection")
    pieces = []
    for winding in range(lower, upper + 1):
        lifted = other + winding * (2 * PI)
        lo, hi = max(anchor.lo, lifted.lo), min(anchor.hi, lifted.hi)
        if lo <= hi:
            pieces.append(I(lo, hi))
    if not pieces:
        return None
    return I(min(p.lo for p in pieces), max(p.hi for p in pieces))


class LoopScanner(v1.LoopScanner):
    """All v1 simple cycles plus one shared-angle check per parallel corridor.

    A corridor check includes every branch, hence also all two-branch cycles.
    Fixed positive taps and finite phase shifts are supported under the same
    passive/small-electrical-angle assumptions used by v1.
    """

    def __init__(self, kwargs, *, max_length=4, max_boxes=64):
        super().__init__(kwargs, max_length=max_length, max_boxes=max_boxes)
        self.row_ids = [
            j for j, row in enumerate(kwargs["case"]["branch"]) if row[10] > 0
        ]
        self.cycles = sorted(
            [
                *self.cycles,
                *(pair for pair, indices in self.pairs.items() if len(indices) > 1),
            ]
        )
        self.cuts = {cycle: self.make_cuts(cycle) for cycle in self.cycles}

    def make_geometry(self, row):
        if not np.isfinite(row[[8, 9]]).all() or row[8] < 0:
            raise ValueError(
                "requires finite positive transformer tap (zero denotes unity)"
            )
        plain = row.copy()
        plain[8:10] = 0
        e = super().make_geometry(plain)
        # Degree normalization is exact, even at a low ambient precision.
        degrees = Decimal.from_float(float(row[9]))
        with localcontext() as ctx:
            ctx.prec = max(60, len(degrees.as_tuple().digits) + 5)
            degrees = degrees % 360
            if degrees > 180:
                degrees -= 360
            if degrees < -180:
                degrees += 360
        e.update(
            tap=I(float(row[8]) if row[8] != 0 else 1), shift=I(degrees) * PI / 180
        )
        return e

    def internal_voltage(self, index, bus, box=None):
        value = self.voltage[bus] if box is None else I(*box)
        if bus == int(self.rows[index][0]):
            value = value / self.geometry[index]["tap"]
        return value

    def current_cap(self, index, values=None):
        row, e = self.rows[index], self.geometry[index]
        caps = []
        for bus in map(int, row[:2]):
            v = self.internal_voltage(index, bus)
            terminal = e["rating"]
            if values is not None and len(self.incident[bus]) == 1:
                p, q = values[bus]
                physical = self.voltage[bus]
                p = p - I(float(self.buses[bus][4])) * v1.square(physical)
                q = q + I(float(self.buses[bus][5])) * v1.square(physical)
                leaf = (
                    v1.square(I(v1.magnitude(p))) + v1.square(I(v1.magnitude(q)))
                ).sqrt()
                terminal = I(min(terminal.hi, leaf.hi))
            caps.append((terminal / (self.base * I(v.lo)) + e["c"] * I(v.hi)).hi)
        return I(min(caps))

    @lru_cache(None)
    def arc(self, index):
        e = self.geometry[index]
        a, b = map(int, self.rows[index][:2])
        u, v = self.internal_voltage(index, a), self.internal_voltage(index, b)
        q = (v1.square(e["r"]) + v1.square(e["x"])).sqrt() * self.current_cap(index)
        q = q / (2 * (I(u.lo) * I(v.lo)).sqrt())
        if q.hi >= 1:
            raise ValueError("large thermal electrical-angle arc unsupported")
        angle = 2 * v1.atan(q / (1 - v1.square(q)).sqrt())
        if angle.hi + e["center"].hi >= (PI / 2).lo:
            raise ValueError("nonmonotone real-power electrical-angle arc unsupported")
        return angle.hi

    def export_bounds(self, cycle, values, losses):
        """Bound aggregate directed corridor export, not an arbitrary branch.

        Each branch subsequently inherits group_min minus the sum of the other
        parallel ratings. Every rating remains separate in all other cut terms.
        """
        bounds = {}
        for a, b in zip(cycle, (*cycle[1:], cycle[0])):
            for start, end in ((a, b), (b, a)):
                members = set(self.pairs[tuple(sorted((a, b)))])
                best = None
                for nodes, internal, boundary in self.cuts[cycle]:
                    if start not in nodes or end in nodes:
                        continue
                    injection = sum((I(values[n][0].lo) for n in sorted(nodes)), I(0))
                    shunts = sum(
                        (
                            I(
                                (
                                    I(float(self.buses[n][4]))
                                    * v1.square(self.voltage[n])
                                ).hi
                            )
                            for n in sorted(nodes)
                        ),
                        I(0),
                    )
                    loss = sum((losses[i] for i in internal), I(0))
                    other = sum(
                        (
                            I(float(self.rows[i][5]))
                            for i in boundary
                            if i not in members
                        ),
                        I(0),
                    )
                    required = injection - shunts - loss - other
                    if best is None or required.lo > best[0]:
                        best = (
                            required.lo,
                            sorted(nodes),
                            v1.endpoints(injection),
                            v1.endpoints(shunts),
                            v1.endpoints(loss),
                            v1.endpoints(other),
                        )
                bounds[start, end] = best
        return bounds

    def branch_bound(self, index, group):
        if group is None:
            return None
        pair = tuple(sorted(map(int, self.rows[index][:2])))
        others = sum(
            (self.geometry[i]["rating"] for i in self.pairs[pair] if i != index), I(0)
        )
        return ((I(group[0]) - others).lo,)

    def branch_angle(self, index, a, b, ubox, vbox, forward, reverse):
        u = self.internal_voltage(index, a, ubox)
        v = self.internal_voltage(index, b, vbox)
        ubox, vbox = (u.lo, u.hi), (v.lo, v.hi)
        high = self.thermal_upper(index, ubox, vbox)
        reverse_high = self.thermal_upper(index, vbox, ubox)
        low_power = self.power_lower(
            index, ubox, vbox, self.branch_bound(index, forward)
        )
        reverse_power = self.power_lower(
            index, vbox, ubox, self.branch_bound(index, reverse)
        )
        detail = dict(
            branch_row=self.row_ids[index],
            direction=[a, b],
            internal_voltage_boxes=[v1.endpoints(u), v1.endpoints(v)],
            electrical_bounds=[
                None if x is None else str(x)
                for x in (high, reverse_high, low_power, reverse_power)
            ],
        )
        if any(x is None for x in (high, reverse_high, low_power, reverse_power)):
            detail["reason"] = "impossible_terminal_circle_or_real_power"
            return None, detail
        lo = max(reverse_high.copy_negate(), low_power)
        hi = min(high, reverse_power.copy_negate())
        if lo > hi:
            detail.update(
                reason="empty_electrical_angle",
                intersection_endpoints=[str(lo), str(hi)],
            )
            return None, detail
        shift = self.geometry[index]["shift"]
        if a != int(self.rows[index][0]):
            shift = -shift
        interval = I(lo, hi) + shift
        detail.update(
            phase_shift_radians=v1.endpoints(shift),
            bus_angle_interval_radians=v1.endpoints(interval),
        )
        return interval, detail

    def prove(self, cycle, bounds, *, max_boxes=None):
        budget = self.max_boxes if max_boxes is None else max_boxes
        initial = tuple((self.voltage[n].lo, self.voltage[n].hi) for n in cycle)
        stack, leaves, visits, nonclosure = [initial], [], 0, 0
        pairs = list(zip(cycle, (*cycle[1:], cycle[0])))
        while stack and visits < budget:
            box = stack.pop()
            visits += 1
            closure, impossible = I(0), False
            edge_angles, branch_details = [], []
            for j, (a, b) in enumerate(pairs):
                intersection = None
                members = self.pairs[tuple(sorted((a, b)))]
                for position, index in enumerate(members):
                    interval, detail = self.branch_angle(
                        index,
                        a,
                        b,
                        box[j],
                        box[(j + 1) % len(cycle)],
                        bounds[a, b],
                        bounds[b, a],
                    )
                    branch_details.append(detail)
                    if interval is None:
                        impossible = True
                        break
                    intersection = (
                        interval
                        if position == 0
                        else intersect_modulo(intersection, interval)
                    )
                    if intersection is None:
                        detail["reason"] = "parallel_branch_angle_conflict_modulo_2pi"
                        impossible = True
                        break
                if impossible:
                    break
                edge_angles.append(v1.endpoints(intersection))
                closure = closure + intersection
            gap = None if impossible else v1.winding_gap(closure)
            if impossible or gap is not None:
                nonclosure += int(not impossible)
                leaves.append(
                    dict(
                        voltage_boxes=[[str(x) for x in pair] for pair in box],
                        reason="empty_edge_angle" if impossible else "cycle_nonclosure",
                        branch_details=branch_details,
                        edge_angle_intervals_radians=edge_angles,
                        closure_interval_radians=None
                        if impossible
                        else v1.endpoints(closure),
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
                        aggregate_lower_mw=str(bound[0]),
                        branch_rows=[
                            self.row_ids[i] for i in self.pairs[tuple(sorted(edge))]
                        ],
                        branch_lower_mw=[
                            str(self.branch_bound(i, bound)[0])
                            for i in self.pairs[tuple(sorted(edge))]
                        ],
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

    @lru_cache(None)
    def geometric_root(self, cycle):
        bounds = {(a, b): None for a, b in zip(cycle, (*cycle[1:], cycle[0]))}
        bounds.update({(b, a): None for a, b in list(bounds)})
        return self.prove(cycle, bounds, max_boxes=1)

    def scan_hour(self, pins, offset=0):
        if self.global_reason:
            return super().scan_hour(pins, offset)
        values = v1.injections(self.kwargs, pins, offset)
        losses = {
            i: I((self.base * e["r"] * v1.square(self.current_cap(i, values))).hi)
            for i, e in self.geometry.items()
        }
        records = []
        for cycle in self.cycles:
            record = dict(cycle=list(cycle))
            try:
                for pair in zip(cycle, (*cycle[1:], cycle[0])):
                    for index in self.pairs[tuple(sorted(pair))]:
                        if index not in self.geometry:
                            raise ValueError(self.reasons[index])
                        self.arc(index)
                root = self.geometric_root(cycle)
                if root["status"] == "certified_incompatible":
                    record.update(root)
                else:
                    bounds = self.export_bounds(cycle, values, losses)
                    if not any(b is not None and b[0] > 0 for b in bounds.values()):
                        record.update(
                            status="unresolved_no_export_trigger",
                            geometry_root_checked=True,
                        )
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
        result = super().scan_schedule(schedule, first_hour)
        result.update(
            simple_cycles=sum(len(c) >= 3 for c in self.cycles),
            parallel_corridors=sum(len(c) == 2 for c in self.cycles),
            branch_groups=[
                dict(buses=list(pair), branch_rows=[self.row_ids[i] for i in indices])
                for pair, indices in sorted(self.pairs.items())
                if len(indices) > 1
            ],
        )
        return result
