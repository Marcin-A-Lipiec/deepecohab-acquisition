"""Experiment filenames and conservative discovery, shared by recording and processing."""

from pathlib import Path
import re


def experiment_stem(name):
    value = str(name or "experiment").strip()
    if not value or value in (".", "..") or value.endswith("."):
        raise ValueError("Enter an experiment name that can be used as a filename.")
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', value):
        raise ValueError('Experiment names cannot contain < > : " / \\ | ? * or control characters.')
    if re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", value, re.I):
        raise ValueError(f"{value!r} is a reserved Windows filename; choose another experiment name.")
    return value


def filename(name, kind, extension):
    return f"{experiment_stem(name)}.{kind}.{extension}"


def config_name(path, config=None):
    """Strip the complete config suffix, never leak '.config' into data names."""
    name = Path(path).name if path else ""
    if name.endswith(".config.json"):
        return experiment_stem(name[:-len(".config.json")])
    if name and name.casefold() != "config.json":
        return experiment_stem(Path(name).stem)
    return experiment_stem((config or {}).get("recording", {}).get("name", "experiment"))


def config_path(directory, name):
    return Path(directory) / filename(name, "config", "json")


def unique_path(path):
    """Readable suffixes preserve previous files; callers still use exclusive creation."""
    path = Path(path)
    candidate, number = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}_{number:02d}{path.suffix}")
        number += 1
    return candidate


def available_prefix(directories, stem):
    names = {p.name for root in directories if Path(root).is_dir() for p in Path(root).iterdir()}
    candidate, number = stem, 2
    while any(name == candidate or name.startswith(candidate + "_") or name.startswith(candidate + ".") for name in names):
        candidate = f"{stem}_{number:02d}"
        number += 1
    return candidate


def discover_config(directory, experiment_name=None):
    """Prefer the named config, then legacy config.json, then one compatible JSON.

    Ambiguity and an invalid preferred file are reported instead of silently
    choosing metadata belonging to a different experiment. Partial but recognized
    configs can be loaded for editing; RFID preflight validates completeness.
    """
    from deepecohab_recorder.config_bundle import load_config_bundle
    from deepecohab_recorder.utils import read_json

    root = Path(directory).expanduser()
    preferred = []
    if experiment_name:
        preferred.append(config_path(root, experiment_name))
        preferred.append(root / (experiment_stem(experiment_name) + ".json"))
    preferred.append(root / "config.json")
    from .preprocessing.publication import recover_for_config
    for path in dict.fromkeys(preferred):
        recover_for_config(path)
        if path.is_file():
            load_config_bundle(path)
            return path
    candidates = []
    if root.is_dir():
        for path in sorted(root.iterdir()):
            if path.is_file() and path.suffix.lower() == ".json":
                try:
                    load_config_bundle(path)
                except (ValueError, KeyError, TypeError, OSError):
                    continue
                candidates.append(path)
    if len(candidates) > 1:
        raise ValueError("More than one experiment configuration is present. Select the intended JSON file: " + ", ".join(p.name for p in candidates))
    return candidates[0] if candidates else None
