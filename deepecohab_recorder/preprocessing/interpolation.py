"""Repair isolated missed neighbour detections; optionally extend to longer routes."""

from __future__ import annotations

from collections import defaultdict, deque
from functools import lru_cache
from bisect import bisect_right
import json
import re

from .models import Cancelled, iso
from .readers import channel_id
from deepecohab_recorder.source_identity import split_source
from deepecohab_recorder.topology import allows_no_main_antennas
from .stitching import insert_pass
from .timing import part_for


class PhysicalGraph:
    def __init__(self, layout, boards, excluded=(), devices=()):
        self.edges = defaultdict(set)
        self.tunnel_pairs = {}
        self.antennas = set()
        self.auxiliary = set()
        self.problems = []
        self.default_board = next(iter(boards)) if len(boards) == 1 else None
        cages = {str(c["cell_id"]) for c in layout.get("cages", [])}
        junctions = {str(j["id"]): j for j in layout.get("junctions", [])}
        tunnels = layout.get("tunnels", [])
        if not cages or not tunnels:
            self.problems.append(
                "No complete physical cage/tunnel layout is available."
            )
        for tunnel in tunnels:
            start, end = str(tunnel.get("start_cell_id", "")), str(
                tunnel.get("end_cell_id", "")
            )
            if start not in cages | junctions.keys() or (
                not tunnel.get("dead_end") and end not in cages | junctions.keys()
            ):
                self.problems.append(
                    f"Invalid endpoints in {tunnel.get('name', 'tunnel')}."
                )
                continue
            chain = [(0.0, "N:" + start)]
            sources = []
            antennas = tunnel.get("antennas", [])
            if len(antennas) < (1 if tunnel.get("dead_end") else 2) and not (
                not antennas and allows_no_main_antennas(tunnel, devices)
            ):
                self.problems.append(
                    f"Insufficient antenna assignments in {tunnel.get('name', 'tunnel')}."
                )
            for index, name in enumerate(antennas):
                try:
                    source = self.canonical(name)
                except ValueError as exc:
                    self.problems.append(str(exc))
                    continue
                if source in self.antennas:
                    self.problems.append(
                        f"Antenna {source} is assigned more than once."
                    )
                self.antennas.add(source)
                sources.append(source)
                chain.append(((index + 1) / (len(antennas) + 1), "A:" + source))
                if tunnel.get("dead_end"):
                    self.auxiliary.add(source)
            for name, junction in junctions.items():
                if junction.get("parent_tunnel") == tunnel.get("name"):
                    position = float(junction.get("position", 0.5))
                    if not 0 < position < 1 or any(
                        abs(position - value) < 1e-9 for value, _ in chain
                    ):
                        self.problems.append(f"Ambiguous junction position: {name}.")
                    chain.append((position, "N:" + name))
            if not tunnel.get("dead_end"):
                chain.append((1.0, "N:" + end))
            chain.sort()
            for (_, left), (_, right) in zip(chain, chain[1:]):
                self.edges[left].add(right)
                self.edges[right].add(left)
            for left, right in zip(sources, sources[1:]):
                self.tunnel_pairs[(left, right)] = tunnel.get("name", "tunnel")
                self.tunnel_pairs[(right, left)] = tunnel.get("name", "tunnel")
        unknown_parents = [
            j["id"]
            for j in junctions.values()
            if j.get("parent_tunnel") not in {t.get("name") for t in tunnels}
        ]
        if unknown_parents:
            self.problems.append(
                "Unknown parent tunnels for junctions: " + ", ".join(unknown_parents)
            )
        self.movement_antennas = self.antennas - set(excluded)
        # Collapse cages, junctions and excluded sensors, not their connections.
        # One edge means no other movement antenna is necessarily crossed.
        self.movement_edges = defaultdict(set)
        for antenna in sorted(self.movement_antennas):
            start = "A:" + antenna
            seen, todo = {start}, deque([start])
            while todo:
                node = todo.popleft()
                for neighbor in self.edges[node]:
                    if neighbor in seen:
                        continue
                    seen.add(neighbor)
                    if neighbor.startswith("A:") and neighbor[2:] in self.movement_antennas:
                        self.movement_edges[antenna].add(neighbor[2:])
                    else:
                        todo.append(neighbor)

    def canonical(self, value):
        value = str(value).strip().upper()
        channel, board, port = split_source(value)
        if board and not port:
            return value
        if self.default_board and not port and re.fullmatch(r"[0-9A-P]+", value):
            return channel_id(value, self.default_board)
        raise ValueError(
            f"Assign an unambiguous antenna and board number in the layout: {value!r}."
        )

    def path(self, start, end, forbidden=frozenset()):
        if start not in self.edges or end not in self.edges:
            return None
        previous = {start: None}
        todo = deque([start])
        while todo:
            node = todo.popleft()
            if node == end:
                result = []
                while node is not None:
                    result.append(node)
                    node = previous[node]
                return result[::-1]
            for next_node in sorted(self.edges[node]):
                if next_node not in previous and next_node not in forbidden:
                    previous[next_node] = node
                    todo.append(next_node)
        return None

    @lru_cache(maxsize=4096)
    def minimum_route(self, a, b):
        """Fewest missed movement sensors; ties never choose an arbitrary route."""
        if a not in self.movement_antennas or b not in self.movement_antennas:
            return (), "unmapped_or_disconnected"
        distance, count, previous = {a: 0}, {a: 1}, {a: None}
        todo = deque([a])
        while todo:
            node = todo.popleft()
            for neighbor in sorted(self.movement_edges[node]):
                step = distance[node] + 1
                if neighbor not in distance:
                    distance[neighbor], count[neighbor], previous[neighbor] = step, count[node], node
                    todo.append(neighbor)
                elif distance[neighbor] == step:
                    count[neighbor] = min(2, count[neighbor] + count[node])
        if b not in distance:
            return (), "unmapped_or_disconnected"
        route, node = [], b
        while node is not None:
            route.append(node)
            node = previous[node]
        return tuple(route[::-1][1:-1]), "unique" if count[b] == 1 else "tied"

    @lru_cache(maxsize=4096)
    def missing_route(self, a, b, mode="standard"):
        """Check local neighbours first, irrespective of longer possible detours.

        Standard repairs exactly one uniquely identified missing movement sensor.
        Extended also permits a unique shortest sequence of missing sensors.
        Neither rule imposes an elapsed-time limit between observed detections.
        """
        if mode == "none":
            return (), "disabled"
        if mode not in ("standard", "extended"):
            raise ValueError("Unknown interpolation mode: " + str(mode))
        if a not in self.movement_antennas or b not in self.movement_antennas:
            return (), "unmapped_or_disconnected"
        if a == b or b in self.movement_edges[a]:
            return (), "direct"
        intermediate = self.movement_edges[a] & self.movement_edges[b]
        if len(intermediate) == 1:
            return tuple(sorted(intermediate)), "single_missing"
        if len(intermediate) > 1:
            return (), "ambiguous"
        route, status = self.minimum_route(a, b)
        if status == "unmapped_or_disconnected":
            return (), status
        if status == "tied":
            return (), "ambiguous"
        if mode == "standard":
            return (), "requires_extended"
        return route, "multiple_missing"

    @lru_cache(maxsize=4096)
    def candidate_antennas(self, a, b):
        """Union of permitted minimum-route candidates, without enumerating paths."""
        if a not in self.movement_antennas or b not in self.movement_antennas:
            return ()
        if a == b or b in self.movement_edges[a]:
            return ()
        common = self.movement_edges[a] & self.movement_edges[b]
        if common:
            return tuple(sorted(common))
        def distances(start):
            result, todo = {start: 0}, deque([start])
            while todo:
                node = todo.popleft()
                for neighbor in self.movement_edges[node]:
                    if neighbor not in result:
                        result[neighbor] = result[node] + 1
                        todo.append(neighbor)
            return result
        left, right = distances(a), distances(b)
        if b not in left:
            return ()
        return tuple(sorted(n for n in left.keys() & right.keys() - {a, b}
                            if left[n] + right[n] == left[b]))

    @lru_cache(maxsize=4096)
    def required(self, a, b):
        """Strict graph query retained for callers; not used by the repair policy."""
        candidates, status = self.minimum_route(a, b)
        if status == "unmapped_or_disconnected":
            return (), status
        if a == b:
            return (), "direct"
        start, end = "A:" + a, "A:" + b
        if not candidates:
            return (), "direct"
        required = [
            node
            for node in candidates
            if self.path(start, end, frozenset(["A:" + node])) is None
        ]
        ambiguous = len(required) != len(candidates)
        return tuple(required), "ambiguous" if ambiguous else "required"


def clock_start(row):
    return row["board_start"] if row["board_start"] is not None else row["pc_start"]


def clock_end(row):
    return row["board_end"] if row["board_end"] is not None else row["pc_end"]


def median_query(db, table, column, where, args):
    count = db.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", args).fetchone()[
        0
    ]
    if not count:
        return None, 0
    middle = db.execute(
        f"SELECT {column} FROM {table} WHERE {where} ORDER BY {column} LIMIT ? OFFSET ?",
        [*args, 2 if count % 2 == 0 else 1, (count - 1) // 2],
    ).fetchall()
    return sum(r[0] for r in middle) / len(middle), count


def clock_continuity(epochs):
    # Timing discontinuities still constrain inference, even though they no
    # longer create output files. A reset cannot become an observed transit.
    epoch_boards = {epoch.id: epoch.board for epoch in epochs}
    by_board = defaultdict(list)
    for epoch in epochs:
        by_board[epoch.board].append(epoch.first_pc)
    clock_breaks = sorted(value for values in by_board.values() for value in sorted(values)[1:])

    def continuous(left, right):
        if left["epoch"] != right["epoch"] and epoch_boards[left["epoch"]] == epoch_boards[right["epoch"]]:
            return False
        return bisect_right(clock_breaks, right["pc_start"]) == bisect_right(clock_breaks, left["pc_end"])
    return continuous


def learn_transits(db, graph, settings, boundaries, epochs, diagnostics, cancelled):
    """Learn once from observations and report every mouse × tunnel direction.

    Reporting is independent of whether an estimate is needed for interpolation.
    Unknown medians remain null; a cohort estimate never replaces a mouse median.
    """
    continuous = clock_continuity(epochs)
    # Learn solely from observed periods, within continuous recording coverage.
    order = "SELECT * FROM passes WHERE kind!='ttl' AND interpolated=0 AND antenna IN (SELECT antenna FROM movement_antennas) ORDER BY tag,COALESCE(board_start,pc_start),id"
    previous = None
    for index, row in enumerate(db.execute(order)):
        if index % 5000 == 0 and cancelled():
            raise Cancelled()
        if (
            previous
            and previous["tag"] == row["tag"]
            and previous["part"] == row["part"]
            and part_for(previous["pc_end"], boundaries) == row["part"]
            and continuous(previous, row)
        ):
            key = (previous["antenna"], row["antenna"])
            delta = clock_start(row) - clock_end(previous)
            if (
                key in graph.tunnel_pairs
                and delta >= 0
                and (previous["board_start"] is None) == (row["board_start"] is None)
            ):
                db.execute(
                    "INSERT INTO transits(tag,a,b,gap) VALUES(?,?,?,?)",
                    (row["tag"], *key, delta),
                )
        previous = row
    db.commit()

    tags = set(diagnostics.info.get("configured_animal_tags", []))
    tags.update(row[0] for row in db.execute("SELECT DISTINCT tag FROM passes WHERE kind!='ttl' AND interpolated=0"))
    rows = []
    cohort = {}
    for tag in sorted(tags):
        for (a, b), tunnel in sorted(graph.tunnel_pairs.items(), key=lambda pair: (pair[1], pair[0])):
            if cancelled():
                raise Cancelled()
            value, count = median_query(db, "transits", "gap", "tag=? AND a=? AND b=?", [tag, a, b])
            included = a in graph.movement_antennas and b in graph.movement_antennas
            eligible = included and count >= settings.minimum_transits
            row = dict(tag=tag, from_antenna=a, to_antenna=b, tunnel=tunnel,
                median_ms=None if value is None else value / 1000,
                samples=count, scope="mouse", minimum_samples=settings.minimum_transits,
                eligible=eligible,
                reason="excluded_from_movement_inference" if not included else
                       "no_observed_transits" if not count else
                       "insufficient_observed_transits" if not eligible else "eligible")
            if settings.cohort_fallback:
                selected_value, selected_count, scope = value, count, "mouse"
                if not eligible and included:
                    if (a, b) not in cohort:
                        cohort[a, b] = median_query(db, "transits", "gap", "a=? AND b=?", [a, b])
                    selected_value, selected_count = cohort[a, b]
                    scope = "cohort"
                usable = included and selected_count >= settings.minimum_transits
                row["interpolation_estimate"] = dict(
                    median_ms=selected_value / 1000 if usable else None,
                    samples=selected_count, scope=scope, eligible=usable)
            rows.append(row)
    diagnostics.info["transition_medians"] = rows
    diagnostics.info["transition_medians_policy"] = {
        "scope": "All configured and accepted observed mice; both directions of each adjacent antenna pair in a physical tunnel.",
        "sample_basis": "Consecutive observed movement periods for the same mouse, within compatible clocks and recording coverage; interpolated rows never train medians.",
        "time_basis": "Next first read minus previous last actual read, in milliseconds; board-aligned time when available, otherwise PC time.",
        "minimum_samples_for_interpolation": settings.minimum_transits,
        "missing_values": "Null means no observed transit; medians below the minimum sample count are still reported but are ineligible for mouse-based interpolation.",
        "cohort_fallback": settings.cohort_fallback,
        "layout_problems": list(graph.problems),
        "complete_table": not graph.problems,
    }


def interpolate(
    db, graph, settings, boundaries, epochs, diagnostics, cancelled, progress
):
    if graph.problems:
        raise ValueError(
            "Interpolation needs a complete physical layout:\n"
            + "\n".join(graph.problems[:10])
            + "\nCorrect the layout or switch interpolation off in Advanced options."
        )
    continuous = clock_continuity(epochs)

    @lru_cache(maxsize=10000)
    def transit(tag, a, b):
        value, count = median_query(
            db, "transits", "gap", "tag=? AND a=? AND b=?", [tag, a, b]
        )
        scope = "mouse"
        if count < settings.minimum_transits and settings.cohort_fallback:
            value, count = median_query(db, "transits", "gap", "a=? AND b=?", [a, b])
            scope = "cohort"
        result = (value if count >= settings.minimum_transits else None, count, scope)
        diagnostics.write(
            "transit_median",
            tag=tag,
            from_antenna=a,
            to_antenna=b,
            tunnel=graph.tunnel_pairs.get((a, b)),
            median_ms=None if value is None else value / 1000,
            samples=count,
            scope=scope,
            eligible=result[0] is not None,
        )
        return result

    @lru_cache(maxsize=10000)
    def duration(tag, antenna):
        value, count = median_query(
            db,
            "passes",
            "duration",
            "tag=? AND antenna=? AND interpolated=0 AND kind!='ttl'",
            [tag, antenna],
        )
        return max(1, round(value)) if value is not None else settings.minimum_ms, count

    epoch_by_board = defaultdict(list)
    for epoch in epochs:
        low, high = db.execute(
            "SELECT MIN(pc),MAX(pc) FROM raw WHERE epoch=?", (epoch.id,)
        ).fetchone()
        epoch_by_board[epoch.board].append(
            (
                epoch,
                epoch.start_known if epoch.start_known is not None else low,
                epoch.end_known if epoch.end_known is not None else high,
            )
        )

    # Snapshot observed IDs to disk: inserts must never become input to this pass.
    db.execute(
        "CREATE TEMP TABLE observed_ids AS SELECT id FROM passes WHERE kind!='ttl' AND interpolated=0 AND antenna IN (SELECT antenna FROM movement_antennas)"
    )
    order = "SELECT passes.* FROM passes JOIN observed_ids USING(id) ORDER BY tag,COALESCE(board_start,pc_start),id"
    previous = None
    inserted = 0
    for index, raw in enumerate(db.execute(order)):
        if index % 2000 == 0:
            if cancelled():
                raise Cancelled()
            progress(
                65,
                f"Checking physical routes: {index:,} periods, {inserted:,} insertions",
            )
        row = dict(raw)
        if previous is None or row["tag"] != previous["tag"]:
            previous = row
            continue
        left = previous
        previous = row
        diagnostics.count("interpolation_observed_transitions_checked")
        candidates = graph.candidate_antennas(left["antenna"], row["antenna"])
        if (
            left["part"] != row["part"]
            or part_for(left["pc_end"], boundaries) != row["part"]
            or not continuous(left, row)
        ):
            diagnostics.reject(row["tag"], candidates, "recording_or_clock_boundary")
            diagnostics.count("interpolation_blocked_by_recording_boundary")
            continue
        missing, status = graph.missing_route(
            left["antenna"], row["antenna"], settings.effective_interpolation_mode
        )
        if status == "direct":
            diagnostics.count("interpolation_direct_transitions")
            continue
        context = {
            "tag": row["tag"],
            "previous_observed_id": left["id"],
            "next_observed_id": row["id"],
            "from_antenna": left["antenna"],
            "to_antenna": row["antenna"],
            "selected_missing_antennas": missing,
            "previous_observed_end_datetime": iso(left["pc_end"], settings.timezone),
            "next_observed_start_datetime": iso(row["pc_start"], settings.timezone),
        }
        if status == "ambiguous":
            diagnostics.reject(row["tag"], candidates, "ambiguous_route")
            diagnostics.count("ambiguous_transitions")
            diagnostics.write("ambiguous_transition", **context)
            continue
        if status == "requires_extended":
            diagnostics.reject(row["tag"], candidates, "requires_extended_mode")
            diagnostics.count("interpolation_skipped_requires_extended")
            diagnostics.write("interpolation_skipped", reason="more_than_one_missing_antenna_standard_mode", **context)
            continue
        if not missing:
            diagnostics.count("unmapped_or_disconnected_transitions")
            diagnostics.write("unmapped_transition", **context)
            continue
        inference_type = "standard" if status == "single_missing" else "extended"
        diagnostics.count("interpolation_" + inference_type + "_candidate_transitions")
        if (left["board_start"] is None) != (row["board_start"] is None) or clock_end(
            left
        ) >= clock_start(row):
            diagnostics.reject(row["tag"], missing, "incompatible_clocks_or_overlapping_observations")
            diagnostics.count("interpolation_timing_conflicts")
            diagnostics.write(
                "interpolation_skipped",
                reason="incompatible_clocks_or_overlapping_observations",
                **context,
            )
            continue
        names = [left["antenna"], *missing, row["antenna"]]
        estimates = {
            0: (
                clock_start(left),
                clock_end(left),
                left["pc_start"] - clock_start(left),
                {},
            ),
            len(names)
            - 1: (
                clock_start(row),
                clock_end(row),
                row["pc_start"] - clock_start(row),
                {},
            ),
        }
        durations = {
            i: duration(row["tag"], names[i]) for i in range(1, len(names) - 1)
        }
        # Forward estimates are anchored at the last ACTUAL read, never the padded 10 ms end.
        for i in range(1, len(names) - 1):
            if (
                i - 1 not in estimates
                or (names[i - 1], names[i]) not in graph.tunnel_pairs
            ):
                continue
            gap, n, scope = transit(row["tag"], names[i - 1], names[i])
            if gap is not None:
                start = round(estimates[i - 1][1] + gap)
                estimates[i] = (
                    start,
                    start + durations[i][0] * 1000,
                    estimates[i - 1][2],
                    {
                        "anchor": "previous_end",
                        "median_samples": n,
                        "median_scope": scope,
                        "transit_ms": gap / 1000,
                    },
                )
        # A missing tunnel entrance can be constrained backward by a later observed exit.
        for i in range(len(names) - 2, 0, -1):
            if (
                i in estimates
                or i + 1 not in estimates
                or (names[i], names[i + 1]) not in graph.tunnel_pairs
            ):
                continue
            gap, n, scope = transit(row["tag"], names[i], names[i + 1])
            if gap is not None:
                end = round(estimates[i + 1][0] - gap)
                estimates[i] = (
                    end - durations[i][0] * 1000,
                    end,
                    estimates[i + 1][2],
                    {
                        "anchor": "following_start",
                        "median_samples": n,
                        "median_scope": scope,
                        "transit_ms": gap / 1000,
                    },
                )
        proposals = []
        for i in range(1, len(names) - 1):
            if i not in estimates:
                diagnostics.reject(row["tag"], [names[i]], "insufficient_timing_support")
                diagnostics.count("interpolation_missing_timing_support")
                diagnostics.write(
                    "interpolation_skipped",
                    reason="no_eligible_mouse_tunnel_median",
                    antenna=names[i],
                    **context,
                )
                continue
            start, end, pc_offset, detail = estimates[i]
            pc_start, pc_end = start + pc_offset, end + pc_offset
            lower = max(estimates[j][1] for j in estimates if j < i)
            upper = min(estimates[j][0] for j in estimates if j > i)
            board = split_source(names[i])[1]
            covers = [
                e
                for e, low, high in epoch_by_board[board]
                if low <= pc_start <= pc_end <= high
            ]
            if (
                not (
                    lower <= start <= end <= upper
                    and left["pc_end"] <= pc_start <= pc_end <= row["pc_start"]
                )
                or len(covers) != 1
            ):
                diagnostics.reject(row["tag"], [names[i]], "timing_or_coverage_conflict")
                diagnostics.count("interpolation_timing_or_coverage_conflicts")
                diagnostics.write(
                    "interpolation_skipped",
                    reason="estimated_period_outside_bounds_or_recording_coverage",
                    antenna=names[i],
                    **context,
                )
                continue
            detail.update(
                inference_type=inference_type,
                route_method="unique_common_neighbour" if inference_type == "standard" else "unique_fewest_missing_antennas",
                selected_route=names,
                observed_anchors={
                    "previous": {
                        "antenna": left["antenna"],
                        "actual_end_datetime": iso(left["pc_end"], settings.timezone),
                        "source_file_id": left["file_end"],
                        "source_line": left["line_end"],
                    },
                    "following": {
                        "antenna": row["antenna"],
                        "start_datetime": iso(row["pc_start"], settings.timezone),
                        "source_file_id": row["file_start"],
                        "source_line": row["line_start"],
                    },
                },
                duration_samples=durations[i][1],
                duration_method=(
                    "mouse_antenna_median"
                    if durations[i][1]
                    else "minimum_duration_fallback"
                ),
                previous_observed_id=left["id"],
                next_observed_id=row["id"],
                topology_status=status,
            )
            proposals.append(
                {
                    "epoch": covers[0].id,
                    "antenna": names[i],
                    "tag": row["tag"],
                    "pc_start": pc_start,
                    "pc_end": pc_end,
                    "board_start": None if left["board_start"] is None else start,
                    "board_end": None if left["board_start"] is None else end,
                    "duration": durations[i][0],
                    "reads": 0,
                    "file_start": None,
                    "line_start": None,
                    "file_end": None,
                    "line_end": None,
                    "kind": "rfid",
                    "part": row["part"],
                    "interpolated": 1,
                    "detail": json.dumps(detail),
                }
            )
        for proposal in proposals:
            insert_pass(db, proposal)
            diagnostics.count("interpolated_" + json.loads(proposal["detail"])["inference_type"] + "_periods")
            inserted += 1
    db.commit()
    diagnostics.count("interpolated_periods", inserted)
