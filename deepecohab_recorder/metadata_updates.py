"""Optimistic, recoverable updates to an experiment config and its GUI state."""
from pathlib import Path
from . import SOFTWARE_LABEL, __version__
from .clock import ExperimentClock
from .schema import migrate_config
from .config_bundle import bundle_digest, save_config_bundle

config_digest = bundle_digest


def write_config_update(path, config, expected_digest):
    path = Path(path)
    if config_digest(path) != expected_digest:
        raise ValueError('Experiment configuration or linked GUI state changed during preprocessing. No metadata was overwritten; reload it and retry.')
    config = migrate_config(config)
    clock = ExperimentClock(config['recording']['timeline']['recording_timezone'])
    config['acquisition'].setdefault('_provenance', {}).update(
        software=SOFTWARE_LABEL, software_version=__version__, software_patch=20, updated_at=clock.iso())
    backup = None
    save_config_bundle(path, config, expected_digest)
    return config, backup
