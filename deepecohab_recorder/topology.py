"""Physical EcoHAB graph and direct mappings. No behavioral interpolation."""

from __future__ import annotations

import copy
import math
import re

from deepecohab_recorder.source_identity import canonical_source


def records(value, prefix: str) -> list[dict]:
    if isinstance(value, dict):
        return [
            dict(record, name=record.get("name") or key)
            for key, record in value.items()
            if isinstance(record, dict)
        ]
    if isinstance(value, list):
        return [dict(record) for record in value if isinstance(record, dict)]
    return []


def distance_errors(tunnel):
    """Optional measurements follow the ordered antennas, one per adjacent pair."""
    length = tunnel.get("length")
    if length is not None and (isinstance(length, bool) or not isinstance(length, (int, float)) or not math.isfinite(length) or length <= 0):
        return [f"{tunnel.get('name', 'Tunnel')}: length must be positive finite centimetres, or null."]
    if "antenna_distances_cm" not in tunnel:
        return []
    distances = tunnel["antenna_distances_cm"]
    name = tunnel.get("name", "Tunnel")
    if not isinstance(distances, list) or len(distances) != max(0, len(tunnel.get("antennas", [])) - 1):
        return [f"{name}: antenna_distances_cm must have one entry per successive antenna pair."]
    if any(value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0) for value in distances):
        return [f"{name}: antenna distances must be positive finite centimetres, or null when unknown."]
    return []


def normalize_layout(value: dict | None, boards: dict | None = None) -> dict:
    out = copy.deepcopy(value or {})
    cages = records(out.get("cages"), "cage")
    tunnels = records(out.get("tunnels"), "tunnel")
    for index, cage in enumerate(cages, 1):
        number = str(
            cage.get("cage_no")
            or str(cage.get("name", "")).removeprefix("cage_")
            or index
        )
        cage["name"] = f"cage_{number}"
        cage.setdefault("cell_id", f"C{number}")
        cage.setdefault("cage_type", "standard")
        cage.setdefault("antennas", [])
        cage.pop("cage_no", None)
    used = set()
    for index, tunnel in enumerate(tunnels, 1):
        match = re.search(r"\d+", str(tunnel.get("name", "")))
        number = int(tunnel.get("tunnel_no") or (match[0] if match else index))
        while number in used:
            number += 1
        used.add(number)
        tunnel.update(name=f"tunnel_{number}", tunnel_no=number)
        labels = tunnel.get("antenna_sources") or tunnel.get("antennas") or []
        board_ids = tunnel.get("antenna_boards") or []
        tunnel["antennas"] = [
            canonical_source(
                label, int(board_ids[i]) if i < len(board_ids) else None, boards
            )
            for i, label in enumerate(labels)
        ]
        if "length" not in tunnel:
            legacy_errors = distance_errors(tunnel)
            if legacy_errors:
                raise ValueError("\n".join(legacy_errors))
        if len(tunnel["antennas"]) == 2:
            if "length" not in tunnel:
                tunnel["length"] = (tunnel.get("antenna_distances_cm") or [None])[0]
            tunnel["antenna_distances_cm"] = [tunnel["length"]]
        else:
            tunnel["length"] = None
        errors = distance_errors(tunnel)
        if errors:
            raise ValueError("\n".join(errors))
        tunnel["antenna_count"] = len(tunnel["antennas"])
        tunnel.setdefault("dead_end", False)
        tunnel.pop("antenna_sources", None)
        tunnel.pop("antenna_boards", None)
    out.update(cages=cages, tunnels=tunnels)
    out.setdefault("junctions", [])
    editor = out.setdefault("editor", {})
    positions = editor.setdefault("cage_positions", {})
    for index, cage in enumerate(cages):
        # Stable initial positions also support logical C1-style cell IDs.
        angle = 2 * math.pi * index / max(1, len(cages)) - math.pi / 2
        positions.setdefault(
            cage["cell_id"], [400 + 270 * math.cos(angle), 350 + 240 * math.sin(angle)]
        )
    editor.setdefault("label_offsets", {})
    for alias in ("antenna_combinations_interp", "antenna_combinations_interpolated"):
        out.pop(alias, None)
    combinations, mapping, errors = direct_maps(out)
    out["antenna_combinations"] = combinations
    out["tunnels_map"] = mapping
    # Errors are returned by validate_layout, never persisted as synthetic relations.
    return out


def direct_maps(layout: dict) -> tuple[dict, dict, list[str]]:
    cages = {c["cell_id"]: c for c in layout.get("cages", [])}
    junctions = {j["id"]: j for j in layout.get("junctions", [])}
    combinations, tunnels_map, errors = {}, {}, []
    incident = {cell: [] for cell in cages}

    def put(target, key, value):
        if key in target and target[key] != value:
            errors.append(f"Ambiguous physical relation {key}: {target[key]} / {value}")
        else:
            target[key] = value

    for tunnel in layout.get("tunnels", []):
        antennas = tunnel.get("antennas", [])
        start, end = tunnel.get("start_cell_id"), tunnel.get("end_cell_id")
        if tunnel.get("dead_end") or start in junctions or end in junctions:
            # Special dead-end interpretation is deliberately deferred.
            continue
        if start not in cages or end not in cages or len(antennas) < 2:
            continue
        if any(not a or a == "?" for a in antennas):
            continue
        a, b = antennas[0], antennas[-1]
        ca, cb = cages[start]["name"].removeprefix("cage_"), cages[end][
            "name"
        ].removeprefix("cage_")
        forward, backward = f"c{ca}_c{cb}", f"c{cb}_c{ca}"
        # A tunnel with additional physical antennas records only adjacent transitions.
        for left, right in zip(antennas, antennas[1:]):
            put(combinations, f"{left}_{right}", forward)
            put(combinations, f"{right}_{left}", backward)
        put(tunnels_map, forward, tunnel["name"])
        put(tunnels_map, backward, tunnel["name"])
        incident[start].append(a)
        incident[end].append(b)
    for cell, antennas in incident.items():
        cages[cell]["antennas"] = list(dict.fromkeys(antennas))
        for a in antennas:
            for b in antennas:
                put(combinations, f"{a}_{b}", cages[cell]["name"])
    return combinations, tunnels_map, errors


def allows_no_main_antennas(tunnel, devices=()):
    """A sensor-free terminal spur cannot supply movement evidence.

    Accept empty dead ends even when a device is labelled with its cage rather
    than the spur. Through tunnels need an explicit device designation before
    their main antenna count can be zero. Keep all their physical connections.
    """
    return bool(tunnel.get('dead_end') or any(
        d.get('position') == tunnel.get('name') for d in devices))


def validate_layout(layout: dict, boards: dict | None = None, devices=()) -> list[str]:
    errors = []
    cages = layout.get("cages", [])
    tunnels = layout.get("tunnels", [])
    junctions = layout.get("junctions", [])
    if not cages:
        errors.append("EcoHAB layout: add at least one cage.")
    if not tunnels:
        errors.append("EcoHAB layout: add at least one tunnel.")
    cells = [c.get("cell_id") for c in cages]
    names = [c.get("name") for c in cages]
    tunnel_names = [t.get("name") for t in tunnels]
    ids = [j.get("id") for j in junctions]
    if len(set(cells)) != len(cells) or len(set(names)) != len(names):
        errors.append("Cage identifiers and names must be unique.")
    if len(set(tunnel_names)) != len(tunnel_names) or len(set(ids)) != len(ids):
        errors.append("Tunnel and junction identifiers must be unique.")
    for cage in cages:
        if not cage.get("name") or not cage.get("cell_id") or not cage.get("cage_type"):
            errors.append("Each cage needs a name, cell ID and cage type.")
    assigned = set()
    board_by_id = {
        str(b.get("board_id", "")): b
        for b in sorted(
            (boards or {}).values(), key=lambda board: bool(board.get("active"))
        )
        if b.get("board_id")
    }
    active_ports = [
        str(board.get("com_port", "")).upper()
        for board in (boards or {}).values()
        if board.get("active") and board.get("com_port")
    ]
    if len(set(active_ports)) != len(active_ports):
        errors.append("Active RFID boards must use distinct COM ports.")
    for tunnel in tunnels:
        name = tunnel.get("name", "Tunnel")
        errors.extend(distance_errors(tunnel))
        if tunnel.get("start_cell_id") not in cells + ids:
            errors.append(f"{name}: invalid start endpoint.")
        if not tunnel.get("dead_end") and tunnel.get("end_cell_id") not in cells + ids:
            errors.append(f"{name}: invalid end endpoint.")
        if not tunnel.get("dead_end") and tunnel.get("start_cell_id") == tunnel.get(
            "end_cell_id"
        ):
            errors.append(f"{name}: endpoints must differ.")
        antennas = tunnel.get("antennas", [])
        if len(antennas) < (1 if tunnel.get("dead_end") else 2) and not (
            not antennas and allows_no_main_antennas(tunnel, devices)
        ):
            errors.append(
                f"{name}: assign {'one' if tunnel.get('dead_end') else 'at least two'} antenna(s)."
            )
        for antenna in antennas:
            match = re.fullmatch(r"([0-9A-P]+)-([1-9]\d*)", str(antenna))
            if not match:
                errors.append(
                    f"{name}: assign an antenna and board number for {antenna!r}."
                )
                continue
            if antenna in assigned:
                errors.append(f"Antenna {antenna} is assigned more than once.")
            assigned.add(antenna)
            if boards is not None:
                board = board_by_id.get(match[2])
                if not board or (not board.get("offline") and not (board.get("active") and board.get("com_port"))):
                    errors.append(f"{antenna}: no configured or archived board with this number.")
                else:
                    from deepecohab_recorder.protocols.rfid import protocol_for_hardware

                    if (
                        match[1]
                        not in protocol_for_hardware(
                            board.get("hardware", "")
                        ).rfid_channels
                    ):
                        errors.append(
                            f"{antenna}: channel incompatible with the selected hardware."
                        )
    parent_by_junction = {j.get("id"): j.get("parent_tunnel") for j in junctions}
    tunnel_by_name = {t.get("name"): t for t in tunnels}
    for junction in junctions:
        if (
            junction.get("parent_tunnel") not in tunnel_names
            or not 0 < float(junction.get("position", 0.5)) < 1
        ):
            errors.append(
                f"{junction.get('id')}: invalid parent tunnel or attachment position."
            )

    def visit(name, stack):
        if name in stack:
            errors.append(f"Cyclic branch attachment at {name}.")
            return
        for endpoint in (
            tunnel_by_name.get(name, {}).get("start_cell_id"),
            tunnel_by_name.get(name, {}).get("end_cell_id"),
        ):
            parent = parent_by_junction.get(endpoint)
            if parent:
                visit(parent, stack | {name})

    for name in tunnel_names:
        visit(name, set())
    errors.extend(direct_maps(copy.deepcopy(layout))[2])
    return list(dict.fromkeys(errors))


def preset_layout(count: int = 4) -> dict:
    cages = [
        {"name": f"cage_{i}", "cell_id": f"C{i}", "cage_type": "standard"}
        for i in range(1, count + 1)
    ]
    tunnels = []
    for i in range(1, count + 1):
        tunnels.append(
            {
                "name": f"tunnel_{i}",
                "tunnel_no": i,
                "start_cell_id": f"C{i}",
                "end_cell_id": f"C{i % count + 1}",
                "dead_end": False,
                "antennas": ["?", "?"],
            }
        )
    positions = (
        {"C1": [120, 600], "C2": [120, 120], "C3": [600, 120], "C4": [600, 600]}
        if count == 4
        else {}
    )
    return normalize_layout(
        {"cages": cages, "tunnels": tunnels, "editor": {"cage_positions": positions}}
    )


def validate_structure(layout):
    """Geometry/reference checks allow draft antennas while protecting topology."""
    errors = []
    cages = {c["cell_id"] for c in layout.get("cages", [])}
    tunnels = {t["name"]: t for t in layout.get("tunnels", [])}
    junctions = {j["id"]: j for j in layout.get("junctions", [])}
    if len(tunnels) != len(layout.get("tunnels", [])) or len(junctions) != len(
        layout.get("junctions", [])
    ):
        errors.append("Tunnel and junction identities must be unique.")
    endpoints = cages | junctions.keys()
    pairs = set()
    for name, tunnel in tunnels.items():
        start, end = tunnel.get("start_cell_id"), tunnel.get("end_cell_id")
        if start not in endpoints or (
            not tunnel.get("dead_end") and end not in endpoints
        ):
            errors.append(f"{name}: unknown endpoint.")
        if not tunnel.get("dead_end"):
            pair = frozenset((start, end))
            if start == end or pair in pairs:
                errors.append(f"{name}: duplicate or self connection.")
            pairs.add(pair)
    for jid, junction in junctions.items():
        if (
            junction.get("parent_tunnel") not in tunnels
            or not 0 < float(junction.get("position", 0.5)) < 1
        ):
            errors.append(f"{jid}: invalid parent tunnel or attachment.")

    def visit(name, active):
        if name in active:
            return True
        tunnel = tunnels.get(name, {})
        return any(
            visit(junctions[end]["parent_tunnel"], active | {name})
            for end in (tunnel.get("start_cell_id"), tunnel.get("end_cell_id"))
            if end in junctions
        )

    if any(visit(name, set()) for name in tunnels):
        errors.append("Recursive junction geometry is not allowed.")
    return errors
