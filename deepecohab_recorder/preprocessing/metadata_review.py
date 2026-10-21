"""Review incomplete experiment metadata without inventing scientific inputs."""

import re

from deepecohab_recorder.schema import metadata_boards, validate_config
from deepecohab_recorder.topology import validate_layout
from .layout_check import check_layout
from .interpolation import PhysicalGraph


def review_metadata(config, settings):
    issues = validate_config(config)
    recording = config["recording"]
    animals = recording["cohort"]["animals"]
    tags = [str(animal.get("tag") or "").strip().upper() for animal in animals]
    incomplete_tags = (not tags or len(tags) != recording["cohort"]["n_mice"] or
                       len(set(tags)) != len(tags) or
                       any(not re.fullmatch(r"[0-9A-F]+", tag) for tag in tags))
    layout = recording["layout"]
    assignments = check_layout(config, settings.inputs)
    graph = PhysicalGraph(layout, {spec.board_id for spec in settings.inputs},
                          devices=recording.get("devices", []))
    incomplete_layout = bool(validate_layout(layout, metadata_boards(config), recording.get("devices", [])) or
                             graph.problems or any(assignments.values()))
    changes = {}
    if settings.whitelist and incomplete_tags:
        changes["whitelist"] = False
    if settings.effective_interpolation_mode != "none" and incomplete_layout:
        changes.update(interpolate=False, interpolation_mode="none")
    return dict(issues=issues, changes=changes, incomplete_layout=incomplete_layout)


def apply_metadata_override(config, settings):
    review = review_metadata(config, settings)
    for key, value in review["changes"].items():
        setattr(settings, key, value)
    return review
