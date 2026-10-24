"""Configuration v8 migrations and validators, independent of Qt and hardware."""

from __future__ import annotations
from deepecohab_recorder import SOFTWARE_LABEL, __version__
from deepecohab_recorder.live_boards import RFID_BOARD_IDS, RFID_DEVICE_KEYS, visible_board_count
from deepecohab_recorder.clock import schedule_defaults, validate_schedule, establish_schedule
import copy
from dataclasses import asdict
from deepecohab_recorder.event_metadata import flat_events, normalize_position, event_row_count, compartment_names, positions
from datetime import date
import re
from deepecohab_recorder.clock import ExperimentClock, UTC, hhmm
from deepecohab_recorder.experiment_timing import normalize_timing, select_timing
from deepecohab_recorder.models import (
    BoardConfig,
    CameraConfig,
    TriggerConfig,
)
from deepecohab_recorder.topology import (
    preset_layout,
    normalize_layout,
    validate_layout,
)

CONFIG_VERSION = 8
ANIMAL_FIELDS = (
    "tag",
    "row_index",
    "mouse_line",
    "genotype",
    "subject_name",
    "sex",
    "date_of_birth",
    "genetic_background",
    "treatment",
    "notes",
)


def default_config(name="experiment"):
    return {
        "version": CONFIG_VERSION,
        "recording": {
            "name": name,
            "project_name": "",
            "recording_location": "",
            "notes": "",
            "timeline": {
                "start_datetime": None,
                "end_datetime": None,
                "datetime_mode": "automatic",
                "recording_timezone": "Europe/Warsaw",
                "start_from": "dark_phase",
                "end_with": "dark_phase",
                "phases": {"light_phase": "00:00", "dark_phase": "12:00"},
                "phase_schedule": schedule_defaults(),
            },
            "cohort": {"n_mice": 0, "animals": []},
            "events": {"event_types": [], "bouts": []},
            "layout": preset_layout(),
            "devices": [],
        },
        "acquisition": {
            "boards": {f"board_{i}": asdict(BoardConfig(i, i == 1)) for i in RFID_BOARD_IDS},
            "camera": asdict(CameraConfig()),
            "hardware_trigger": asdict(TriggerConfig()),
            "environment": {
                "lighting": {
                    "enabled": False,
                    "com_port": "",
                    "baudrate": 115200,
                    "device_uid": None,
                }
            },
            "launch": {
                **{key: key == "board1" for key in RFID_DEVICE_KEYS},
                "camera": False,
                "trigger": False,
                "externally_triggered": True,
                "delay_seconds": 2.0,
            },
            "com_ports": {},
            "preprocessing": {"sources": [], "options": {}},
            "ui": {"events_row_count": 4, "rfid_board_count": 1},
            "_provenance": {"software": SOFTWARE_LABEL, "software_version": __version__},
        },
    }


def merge(base, incoming):
    result = copy.deepcopy(base)
    for key, value in incoming.items():
        result[key] = (
            merge(result[key], value)
            if isinstance(value, dict) and isinstance(result.get(key), dict)
            else copy.deepcopy(value)
        )
    return result


def retire_lickometer_settings(config):
    """Keep old USB settings as inert history; never restore a recorder/COM role.

    Mutates the supplied config, including on save. Idempotent, and preserves
    unfamiliar legacy payloads without requiring them to pass new validation.
    """
    acquisition = config.setdefault("acquisition", {})
    retired = []
    for owner, key, path in (
        (config, "lickometer", "lickometer"),
        (acquisition, "lickometer", "acquisition.lickometer"),
        (acquisition.get("launch", {}), "lickometer", "acquisition.launch.lickometer"),
    ):
        if isinstance(owner, dict) and key in owner:
            retired.append({"source": path, "value": owner.pop(key)})
    ports = acquisition.get("com_ports", {})
    if isinstance(ports, dict):
        for port, role in list(ports.items()):
            if isinstance(role, str) and role.strip().lower().startswith("lickometer"):
                retired.append(
                    {
                        "source": "acquisition.com_ports." + port,
                        "value": ports.pop(port),
                    }
                )
    if retired:
        archive = acquisition.setdefault("_legacy_lickometer", [])
        if not isinstance(archive, list):
            archive = acquisition["_legacy_lickometer"] = [archive]
        for entry in retired:
            if entry not in archive:
                archive.append(entry)
    return config


def normalize_animals(value):
    result = copy.deepcopy(value)
    rows = result.get("animals", result.get("rows", []))
    if isinstance(rows, dict):
        rows = [
            dict(
                r,
                tag=r.get(
                    "tag", r.get("tag_no", k if not k.startswith("row_") else "")
                ),
            )
            for k, r in rows.items()
        ]
    animals = []
    for index, source in enumerate(rows, 1):
        row = dict(source)
        row["tag"] = str(row.get("tag", row.get("tag_no", "")) or "").strip().upper()
        if "date_of_birth" not in row and row.get("dob"):
            row["date_of_birth"] = row.pop("dob")
        row["subject_name"] = str(
            row.get("subject_name", row.get("mouse_id", row.get("number", ""))) or ""
        )
        row["row_index"] = int(row.get("row_index") or index)
        for key in ANIMAL_FIELDS:
            row.setdefault(key, "")
        for key in ("subject_name", "treatment"):
            row[key] = str(row.get(key) or "").strip() or None
        row["notes"] = str(row.get("notes") or "")
        sex = str(row.get("sex") or "").strip()
        row["sex"] = {"m": "Male", "male": "Male", "f": "Female", "female": "Female"}.get(sex.casefold(), sex)
        for key in (
            "tag_no",
            "mouse_id",
            "number",
            "age",
            "age_value",
            "age_unit",
            "age_input_mode",
        ):
            row.pop(key, None)
        animals.append(row)
    result.pop("rows", None)
    result.pop("age_input_mode", None)
    result.update(animals=animals, n_mice=int(result.get("n_mice", len(animals))))
    return result


def normalize_events(events, clock, recording_end=None):
    from .event_metadata import normalize_event_devices
    events = flat_events(events)
    result, types, bouts = copy.deepcopy(events), {}, []
    for event_type in events.get("event_types", []):
        name, description = str(event_type.get("name", "")).strip(), str(
            event_type.get("description", "")
        )
        if not name:
            continue
        if name in types and types[name]["description"] != description:
            raise ValueError(f"Event {name!r} has conflicting descriptions.")
        devices = normalize_event_devices(event_type.get('devices'))
        if name in types and types[name]['devices'] != devices:
            raise ValueError(f'Event {name!r} has conflicting devices.')
        types[name] = dict(event_type, name=name, description=description, devices=devices)
    for source in events.get("bouts", []):
        bout = dict(source)
        bout["position"] = normalize_position(bout.get("position"))
        name = str(bout.get("name", "")).strip()
        if name not in types:
            raise ValueError(f"Event bout {name!r} has no event type.")
        if not bout.get("start"):
            raise ValueError(f"Event {name!r} needs a start date and time.")
        start = clock.parse(bout["start"], allow_naive=True)
        specified = bool(bout.get("end_user_specified", bool(bout.get("end"))))
        end_text = (
            bout.get("end")
            if specified or bout.get("finalized_run_id")
            else recording_end
        )
        end = clock.parse(end_text, allow_naive=True) if end_text else None
        if specified and end is None:
            raise ValueError(f"Event {name!r}: a specified end cannot be empty.")
        if end and end.astimezone(UTC) < start.astimezone(UTC):
            if specified:
                raise ValueError(f"Event {name!r}: end precedes start.")
            end = None
            bout["recording_ended_before_event"] = True
        bout.update(
            name=name,
            start=start.isoformat(),
            end=end.isoformat() if end else None,
            end_user_specified=specified,
        )
        bouts.append(bout)
    result.update(event_types=list(types.values()), bouts=bouts)
    return result


def migrate_config(source, current_experiment_name=None):
    if not isinstance(source, dict):
        raise ValueError("Config must be a JSON object.")
    old = copy.deepcopy(source)
    modern, version = isinstance(old.get("recording"), dict), old.get("version")
    if modern and version not in (None, 1, 2, 3, 4, 5, 6, 7, CONFIG_VERSION):
        raise ValueError(
            f"Unsupported recording config version {version}; current version is {CONFIG_VERSION}."
        )
    if not modern and not any(
        k in old for k in ("experiment", "animals", "layout", "daq_com_port")
    ):
        raise ValueError("Unrecognized experiment config structure.")
    if not modern and version not in (None, 1, 2, 3, 4, 16):
        raise ValueError(f"Unsupported legacy recorder format {version}.")
    if "acqusition" in old:
        if "acquisition" in old and old["acquisition"] != old["acqusition"]:
            raise ValueError("Conflicting acquisition and acqusition sections.")
        old["acquisition"] = old.pop("acqusition")
    name = current_experiment_name or (
        old.get("recording") or old.get("experiment") or {}
    ).get("name", "experiment")
    result = merge(default_config(name), old)
    # Infer old panel counts before defaults can hide meaningful saved hardware.
    result["acquisition"].setdefault("ui", {})["rfid_board_count"] = visible_board_count(old.get("acquisition", {}))
    recording, acquisition = result["recording"], result["acquisition"]
    # The public v7 contract stores selected sources in acquisition. Runtime
    # timing keeps its evidence together, including automatic recovery flags.
    if 'datetime_provenance' in acquisition:
        recording['timeline']['datetime_provenance'] = acquisition.pop('datetime_provenance')
    # New-experiment presets must not be injected into imported empty/legacy data.
    recording["layout"] = copy.deepcopy(
        (old.get("recording") or {}).get("layout", old.get("layout", {}))
    )
    old_schedule = (
        (old.get("recording") or {}).get("timeline", {}).get("phase_schedule")
    )
    if not modern:
        experiment = old.get("experiment", {})
        recording["timeline"] = merge(
            recording["timeline"],
            {
                k: experiment[k]
                for k in ("start_datetime", "end_datetime", "recording_timezone")
                if k in experiment
            },
        )
        recording["cohort"] = normalize_animals(old.get("animals", {}))
        recording["layout"] = old.get("layout", {})
        recording["notes"] = old.get("notes", "")
        recording["recording_location"] = old.get("recording_location", "")
        recording["events"] = old.get("events", {"event_types": [], "bouts": []})
        for key in (
            "camera",
            "hardware_trigger",
            "environment",
            "launch",
        ):
            acquisition[key] = merge(acquisition[key], old.get(key, {}))
        if old.get("camera_trigger"):
            acquisition["camera"] = merge(acquisition["camera"], old["camera_trigger"])
        if not (old.get("acquisition") or {}).get("boards"):
            acquisition["boards"]["board_1"]["com_port"] = old.get("daq_com_port") or (
                old.get("acquisition") or {}
            ).get("com_port", "")
        for key in (
            "experiment",
            "animals",
            "layout",
            "notes",
            "recording_location",
            "events",
            "camera",
            "hardware_trigger",
            "environment",
            "launch",
            "camera_trigger",
            "daq_com_port",
        ):
            result.pop(key, None)
    # Normalize legacy hardware aliases before defaults mask valid zero values.
    hardware_source = old.get("acquisition", {}) if modern else old
    for section, aliases in {
        "camera": {
            "target_width": "width",
            "target_height": "height",
            "segment_time_s": "segment_seconds",
            "preview_max_fps": "preview_fps",
            "software_resize_if_needed": "software_resize",
        },
        "hardware_trigger": {"mode": "train_mode"},
    }.items():
        raw_section = hardware_source.get(section, {})
        if section == "camera" and not modern:
            raw_section = merge(old.get("camera_trigger", {}), raw_section)
        for old_key, new_key in aliases.items():
            if new_key not in raw_section and old_key in raw_section:
                acquisition[section][new_key] = raw_section[old_key]
            acquisition[section].pop(old_key, None)
        if section == "hardware_trigger":
            duration = (
                raw_section.get("duration_dd_hh_mm")
                or raw_section.get("duration")
                or {}
            )
            for part, key in [
                ("dd", "duration_days"),
                ("hh", "duration_hours"),
                ("mm", "duration_minutes"),
            ]:
                if key not in raw_section and part in duration:
                    acquisition[section][key] = duration[part]
            acquisition[section].pop("duration_dd_hh_mm", None)
            acquisition[section].pop("duration", None)
    recording["name"] = name
    recording["cohort"] = normalize_animals(recording["cohort"])
    timeline, environment = recording["timeline"], acquisition["environment"]
    for key, value in timeline["phases"].items():
        if isinstance(value, str) and re.fullmatch(r"\d{2}:\d{2}:00", value):
            timeline["phases"][key] = value[:5]
    source_phases = (old.get("recording") or {}).get("timeline", {}).get("phases")
    if not source_phases:
        timeline["phases"] = {
            "light_phase": environment.get("light_start_hhmm", "00:00"),
            "dark_phase": environment.get("light_end_hhmm", "12:00"),
        }
    elif any(
        environment.get(k) not in (None, "", timeline["phases"][phase])
        for k, phase in (
            ("light_start_hhmm", "light_phase"),
            ("light_end_hhmm", "dark_phase"),
        )
    ):
        raise ValueError(
            "Environment and timeline contain conflicting phase schedules."
        )
    environment.pop("advanced_control", None)
    for key in ("light_start_hhmm", "light_end_hhmm"):
        environment.pop(key, None)
    retire_lickometer_settings(result)
    acquisition["channel_identity_format"] = "{physical_channel}-{board_id}"
    if not isinstance(acquisition.get("com_ports"), dict):
        acquisition["com_ports"] = {}
    for board in acquisition["boards"].values():
        board["raw_log_enabled"] = True
    recording["layout"] = normalize_layout(recording["layout"], identity_boards(result))
    from .device_antennas import normalize_devices, layout_defaults, migrate_device_references
    old_recording = old.get('recording', {})
    if 'device_antennas' in old_recording and 'devices' in old_recording and old_recording['devices'] and old_recording['devices'] != old_recording['device_antennas']:
        raise ValueError('Conflicting devices and legacy device_antennas sections.')
    rows = old_recording.get('device_antennas', recording.get('devices', []))
    recording.pop('device_antennas', None)
    recording["devices"] = normalize_devices(rows, identity_boards(result))
    migrate_device_references(result)
    migrate_source_options(result)
    layout_defaults(result, acquisition.setdefault("preprocessing", {}).setdefault("options", {}), strict=False)
    clock = ExperimentClock(timeline["recording_timezone"])
    for section in (
        "environment",
        "camera",
        "hardware_trigger",
        "_provenance",
    ):
        metadata = acquisition.get(section, {})
        for field in (
            "started_iso",
            "stopped_iso",
            "sent_iso",
            "generated_at",
            "updated_at",
            "start_datetime",
            "end_datetime",
        ):
            if isinstance(metadata.get(field), str) and metadata[field]:
                metadata[field] = clock.parse(
                    metadata[field], allow_naive=True
                ).isoformat()
    for key in ("start_datetime", "end_datetime"):
        timeline[key] = (
            clock.parse(timeline[key], allow_naive=True).isoformat()
            if timeline.get(key)
            else None
        )
    recording["events"] = normalize_events(
        recording["events"], clock, timeline.get("end_datetime")
    )
    if not old_schedule or old_schedule.get('mode') == 'legacy_local':
        timeline['phase_schedule'] = schedule_defaults(timeline['phases'])
        if timeline.get('start_datetime'):
            timeline = establish_schedule(timeline, timeline['start_datetime'])
    validate_schedule(timeline['phase_schedule'], clock.name)
    provenance = timeline.setdefault('datetime_provenance', {})
    for field in ('start', 'end'):
        if timeline.get(field + '_datetime'):
            provenance.setdefault(field + '_source', 'imported_config')
    recording['timeline'] = normalize_timing(timeline)
    saved_rows = old.get("acquisition", {}).get("ui", {}).get("events_row_count")
    rows = event_row_count(recording["events"])
    acquisition.setdefault("ui", {})["events_row_count"] = rows if saved_rows is None else max(rows, int(saved_rows))
    result["version"] = CONFIG_VERSION
    return result


def identity_boards(config):
    boards = copy.deepcopy(config.get('acquisition', {}).get('boards', {}))
    for index, source in enumerate(config.get('acquisition', {}).get('preprocessing', {}).get('sources', [])):
        boards['source_' + str(index)] = dict(source, com_port=source.get('port', ''))
    return boards


def migrate_source_options(config):
    from .source_identity import canonical_source, canonical_alias
    options = config['acquisition'].get('preprocessing', {}).get('options', {})
    boards = identity_boards(config)
    for key in ('routes', 'ttl_routes', 'aliases'):
        mapped = {}
        for source, value in options.get(key, {}).items():
            name = canonical_source(source, boards=boards)
            target = canonical_alias(value, boards) if key == 'aliases' else value
            if name in mapped and mapped[name] != target:
                raise ValueError(f'Conflicting {key} after board identity migration: {name}.')
            mapped[name] = target
        if key in options:
            options[key] = mapped


def metadata_boards(config, inputs=None):
    """An offline logical-board catalogue, without activating serial connections."""
    from deepecohab_recorder.models import NEW_DAQ, DAQ_V15, OLD_DAQ
    hardware = {'v1.0': NEW_DAQ, 'v1.5': DAQ_V15, 'legacy_raw': OLD_DAQ,
                'old_decimal': OLD_DAQ, 'old_hex': OLD_DAQ}
    boards = copy.deepcopy(config['acquisition']['boards'])
    profiles = {}
    sources = (config['acquisition'].get('preprocessing', {}).get('sources', [])
               if inputs is None else [vars(spec) for spec in inputs])
    for source in sources:
        board_id, profile = source.get('board_id'), source.get('profile')
        if not board_id or profile not in hardware:
            continue
        if board_id in profiles and hardware[profiles[board_id]] != hardware[profile]:
            raise ValueError(f'Board {board_id} has conflicting preprocessing hardware formats.')
        profiles[board_id] = profile
        boards = {k: b for k, b in boards.items() if b.get('board_id') != board_id}
        boards['source_' + str(board_id)] = {'board_id': board_id,
            'com_port': '', 'hardware': hardware[profile], 'active': False, 'offline': True}
    return boards


def animal_required_errors(animals):
    errors = []
    tags = set()
    for index, animal in enumerate(animals, 1):
        tag = str(animal.get("tag") or "").strip().upper()
        if not re.fullmatch(r"[0-9A-F]+", tag) or tag in tags:
            errors.append(f"Animal {index}: enter a unique hexadecimal RFID tag.")
        tags.add(tag)
        try:
            date.fromisoformat(animal.get("date_of_birth") or "")
        except (ValueError, TypeError):
            errors.append(f"Animal {index}: enter a valid date of birth (YYYY-MM-DD).")
        for key in ('tag', 'mouse_line', 'genotype', 'genetic_background', 'date_of_birth'):
            if not str(animal.get(key) or '').strip():
                errors.append(f"Animal {index}: enter {key.replace('_', ' ')}.")
        if animal.get('sex') not in ('Male', 'Female'):
            errors.append(f'Animal {index}: select Male or Female.')
    return errors


def validate_config(config, boards=None):
    errors = []
    try:
        recording = config["recording"]
        cohort, tags = recording["cohort"], set()
        animals = cohort["animals"]
        if not animals:
            errors.append("Animal details: enter at least one animal.")
        if cohort["n_mice"] != len(animals):
            errors.append("Animal count does not match the saved rows.")
        for index, animal in enumerate(animals, 1):
            for field in ANIMAL_FIELDS:
                if field not in animal:
                    errors.append(f"Animal {index}: missing field {field}.")
            tag = str(animal.get("tag", "")).strip().upper()
            if not tag or not re.fullmatch(r"[0-9A-F]+", tag):
                errors.append(f"Animal {index}: enter a hexadecimal RFID tag.")
            elif tag in tags:
                errors.append(f"Animal {index}: duplicate tag {tag}.")
            tags.add(tag)
            for field in ("mouse_line", "genotype", "genetic_background"):
                if not str(animal.get(field) or "").strip():
                    errors.append(f"Animal {index}: enter {field.replace(chr(95), chr(32))}.")
            if animal.get("sex") not in ("Male", "Female"):
                errors.append(f"Animal {index}: select Male or Female.")
            try:
                date.fromisoformat(animal.get("date_of_birth") or "")
            except (ValueError, TypeError):
                errors.append(
                    f"Animal {index}: enter a valid date of birth (YYYY-MM-DD)."
                )
        errors.extend(
            validate_layout(
                recording["layout"],
                boards if boards is not None else metadata_boards(config),
                recording.get("devices", []),
            )
        )
        from .device_antennas import device_errors
        errors.extend(device_errors(recording, boards if boards is not None else metadata_boards(config)))
        timeline = recording["timeline"]
        if timeline.get("end_with") not in ("light_phase", "dark_phase"):
            errors.append("Select the last light or dark phase as experiment end choice.")
        select_timing(timeline, require_manual=True)
        clock = ExperimentClock(timeline["recording_timezone"])
        light, dark = (
            hhmm(timeline["phases"][p]) for p in ("light_phase", "dark_phase")
        )
        if light == dark:
            errors.append("Light and dark phase starts must differ.")
        if timeline["start_from"] not in ("light_phase", "dark_phase"):
            errors.append("Select the first light or dark phase as analysis origin.")
        validate_schedule(
            timeline.get("phase_schedule", schedule_defaults(timeline["phases"])), clock.name
        )
        events = normalize_events(recording["events"], clock, timeline.get("end_datetime"))
        device_names = {d['name'] for d in recording.get('devices', [])}
        for event in events['event_types']:
            unknown = set(event.get('devices') or []) - device_names
            if unknown:
                errors.append(f"Event {event['name']!r}: unknown devices: {', '.join(sorted(unknown))}.")
        known = set(compartment_names(recording["layout"]))
        for bout in events["bouts"]:
            unknown = set(positions(bout.get("position"))) - known
            if unknown:
                errors.append(f"Event {bout['name']!r}: compartments are absent from the layout: {', '.join(sorted(unknown))}.")
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f"Invalid configuration structure: {exc}")
    return errors
