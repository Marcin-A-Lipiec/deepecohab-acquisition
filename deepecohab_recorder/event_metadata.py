"""Grouped event interchange and compartment selections, independent of Qt."""

import copy


def normalize_position(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value or value.lower() == "null":
            return None
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value):
        values = list(dict.fromkeys(v.strip() for v in value))
        return values or None
    raise ValueError("Event position must be null, a compartment name, or a list of compartment names.")


def positions(value):
    value = normalize_position(value)
    return [] if value is None else [value] if isinstance(value, str) else value


def normalize_event_devices(value):
    if value is None or value == []:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError('Event devices must be null or a list of device names.')
    return list(dict.fromkeys(v.strip() for v in value))


def compartment_names(layout):
    return list(dict.fromkeys(str(item["name"]) for section in ("cages", "tunnels") for item in layout.get(section, []) if item.get("name")))


def flat_events(events):
    """Use a stable internal form so existing run-finalization code remains usable."""
    if isinstance(events, dict):
        return copy.deepcopy(events)
    if not isinstance(events, list):
        raise ValueError("Events must be a grouped list or a legacy event_types/bouts object.")
    types, bouts = [], []
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("bouts", []), list):
            raise ValueError("Each event must be an object containing a list of bouts.")
        types.append({k: copy.deepcopy(v) for k, v in event.items() if k != "bouts"})
        for bout in event.get("bouts", []):
            if not isinstance(bout, dict):
                raise ValueError("Each event bout must be an object.")
            if bout.get("name", event.get("name")) != event.get("name"):
                raise ValueError("An event bout name conflicts with its parent event.")
            bouts.append(dict(copy.deepcopy(bout), name=event.get("name", "")))
    return {"event_types": types, "bouts": bouts}


def grouped_events(events):
    events = flat_events(events)
    grouped = {e["name"]: dict(copy.deepcopy(e), devices=normalize_event_devices(e.get('devices')), bouts=[]) for e in events.get("event_types", [])}
    for source in events.get("bouts", []):
        bout = copy.deepcopy(source)
        name = bout.pop("name")
        bout["position"] = normalize_position(bout.get("position"))
        if bout.get("end_user_specified") is True:
            bout.pop("end_user_specified")
        grouped[name]["bouts"].append(bout)
    return list(grouped.values())


def event_row_count(events):
    events = flat_events(events)
    bouts = events.get("bouts", [])
    used = {b["name"] for b in bouts}
    return len(bouts) + sum(e["name"] not in used for e in events.get("event_types", []))


def storage_config(config):
    result = copy.deepcopy(config)
    events = result["recording"]["events"]
    extra = {k: v for k, v in events.items() if k not in ("event_types", "bouts")} if isinstance(events, dict) else {}
    if extra:
        result["acquisition"].setdefault("_provenance", {})["legacy_event_metadata"] = extra
    result["recording"]["events"] = grouped_events(events)
    return result
