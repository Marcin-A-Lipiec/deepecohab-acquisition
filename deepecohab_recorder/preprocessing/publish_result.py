"""Prepare the named data, config pair, diagnostic and README as one file set."""
import copy
from pathlib import Path
from deepecohab_recorder import SOFTWARE_LABEL, __version__

from deepecohab_recorder.config_bundle import (project, links, MAIN_LINK, LOG_LINK,
    relative_link, resolve_link, bundle_digest)
from deepecohab_recorder.dataset_readme import README, readme_text
from deepecohab_recorder.experiment_files import filename
from deepecohab_recorder.utils import atomic_write_json, read_json
from .timing import file_hash
from .models import Cancelled
from .result_state import prior_outputs, private_path, hide_private_state


def publish_result(transaction, settings, config, updated, timing_report, diagnostics, outputs, expected_config, db, cancelled=lambda: False):
    root, output = transaction.root, transaction.output
    name = diagnostics.info['experiment_output_name']
    log_path = root / filename(name, 'diagnostic', 'json')
    previous = prior_outputs(log_path, output, name, config)
    changes = {output / entry['file']: transaction.staging / entry['file'] for entry in outputs}
    for old in previous.keys() - changes.keys():
        changes[old] = None
    for entry in outputs:
        entry['relative_path'] = relative_link(output / entry['file'], log_path)
    completed = dict(created_at=diagnostics.info['created_at'], diagnostic_file=log_path.name,
                     output_files={entry['relative_path']: diagnostics.output_fingerprints[entry['file']] for entry in outputs})
    timing_report['bounds_status'] = timing_report['status']
    timing_report['metadata_status'] = 'no_config_file'
    config_path = None
    old_main = Path(settings.config_path).resolve() if settings.config_path else None
    updated = copy.deepcopy(updated or config)
    if old_main:
        config_path = root / filename(name, 'config', 'json')
        from deepecohab_recorder.clock import ExperimentClock
        updated['acquisition'].setdefault('_provenance', {}).update(
            {LOG_LINK: log_path.name, 'software': SOFTWARE_LABEL, 'software_version': __version__, 'software_patch': 20,
             'updated_at': ExperimentClock(settings.timezone).iso()})
        updated['acquisition'].setdefault('active_inputs', {})['ttl'] = [row[0] for row in db.execute("SELECT DISTINCT antenna FROM raw_counts WHERE kind='ttl' ORDER BY antenna")]
        options = {k: v for k, v in settings.to_dict().items() if k not in ('inputs', 'config_path', 'output_dir', 'parquet', 'csv', 'timezone')}
        for key in ('routes', 'ttl_routes'):
            options[key] = getattr(settings, '_route_overrides', {}).get(key, options.get(key, {}))
        saved = updated['acquisition'].setdefault('preprocessing', {})
        saved.update(options=options, formats={'parquet': settings.parquet, 'csv': settings.csv})
        saved['_output_integrity'] = copy.deepcopy(completed)
        completed.pop('output_files')
        # Keep the last executed settings separate from editable GUI defaults.
        # Later setup edits must not change the context of an existing dataset.
        completed.update(options={**diagnostics.info['processing_options'],
                                  'aliases': settings.aliases, 'routes': settings.routes},
                         stitch_windows_ms=diagnostics.info.get('stitch_windows_ms', {}),
                         excluded_movement_inputs=diagnostics.info.get('movement_antennas', {}).get('excluded_by_output_group', []))
        saved['completed_run'] = completed
        main, state = project(updated, config_path)
        state_path = root / links(main)[MAIN_LINK]
        if state_path.exists():
            owner = read_json(state_path)
            if owner.get('format') != 'acquisition_gui_state' or owner.get('main_config') not in (old_main.name, config_path.name):
                raise ValueError('The new GUI-state filename belongs to a different file or experiment.')
        for target, data in ((config_path, main), (state_path, state)):
            staged = transaction.metadata_staging / target.name
            atomic_write_json(staged, data)
            changes[target] = staged
        if old_main != config_path:
            if config_path.exists():
                raise ValueError('The new main-config filename already exists. Load it before preprocessing.')
            changes[old_main] = None
        old_link = links(read_json(old_main)).get(MAIN_LINK)
        if old_link:
            old_state = resolve_link(old_main, old_link)
            if old_state != state_path and old_state.exists():
                # Only same-folder sidecars can be retired by this transaction.
                # Legacy nested state remains readable historical input.
                if old_state.parent == root and read_json(old_state).get('main_config') == old_main.name:
                    changes[old_state] = None
        timing_report.update(status='updated' if timing_report['status'] == 'proposed' else timing_report['status'], metadata_status='updated', path=str(config_path),
                             backup=None, diagnostic_log_path=log_path.name)
        diagnostics.info['config_file'] = config_path.name
        diagnostics.info['gui_state_file'] = state_path.name
        diagnostics.linked_config = updated
    else:
        # No main config means no GUI state to host safe replacement evidence.
        private = private_path(root, name)
        staged = transaction.metadata_staging / private.name
        atomic_write_json(staged, dict(format='preprocessing_state', experiment=name, completed_run=completed))
        changes[private] = staged
    diagnostics.info['experiment_timing'] = timing_report
    diagnostics.info['output_directory_relative_to_diagnostic'] = relative_link(output, log_path)
    diagnostics.info['recording_history'] = updated.get('acquisition', {}).get('recording_history', {})
    diagnostics.info['device_history'] = updated.get('acquisition', {}).get('device_history', {})
    diagnostics.finish(db)
    changes[log_path] = transaction.staging / diagnostics.filename
    for directory in {root, output}:
        target = directory / README
        staged = (transaction.metadata_staging if directory == root else transaction.staging) / README
        staged.write_text(readme_text(directory, changes, outputs if directory == output else ()), encoding='utf-8')
        changes[target] = staged
    def guard():
        if cancelled():
            raise Cancelled()
        if old_main and bundle_digest(old_main) != expected_config:
            raise ValueError('Experiment metadata changed during preprocessing. Reload and retry; previous results were preserved.')
        for target in [output / e['file'] for e in outputs]:
            if target.exists() and target not in previous:
                raise ValueError(f'Cannot replace {target.name}: it is not owned by the previous preprocessing result.')
        for path, expected_hash in previous.items():
            if path.exists() and (not expected_hash or file_hash(path, lambda: False) != expected_hash):
                raise ValueError(f'{path.name} changed since the previous preprocessing run. Preserve that edit elsewhere before replacing the generated results.')
    transaction.publish(changes, guard)
    if not old_main:
        hide_private_state(private)
    return timing_report
