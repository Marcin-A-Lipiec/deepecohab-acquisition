"""Named root outputs, TTL grouping, safe replacement and compact acquisition evidence."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest
import polars as pl

from deepecohab_recorder.config import ConfigRepository
from deepecohab_recorder.config_bundle import load_config_bundle, save_config_bundle, project, MAIN_LINK, LOG_LINK, links
from deepecohab_recorder.experiment_files import discover_config, filename
from deepecohab_recorder.preprocessing.models import InputFile, Settings, Cancelled
from deepecohab_recorder.preprocessing.pipeline import run
from deepecohab_recorder.preprocessing.inventory import inventory
from deepecohab_recorder.preprocessing.publication import Publication
from deepecohab_recorder.preprocessing.recording_gaps import acquisition_journals
from deepecohab_recorder.preprocessing.readers import scan
from deepecohab_recorder.recording_history import device_state
from deepecohab_recorder.controllers.evidence import MalformedSamples
from deepecohab_recorder.dataset_readme import README
from tests.helpers import valid_config
from tests.test_diagnostics_and_options import gui

TAG = 'D5969C1A'


def experiment(root, name='E', custom=None):
    root.mkdir(exist_ok=True)
    config = valid_config()
    config['recording']['name'] = name
    config['recording']['layout'] = {}
    main = root / filename(name, 'config', 'json')
    save_config_bundle(main, config)
    raw = root / 'raw_antenna_reads'
    raw.mkdir(exist_ok=True)
    source = raw / 'capture.csv'
    source.write_text('\n'.join([
        f'A-1;{TAG};0;2026-09-01T12:00:00+02:00;1',
        f'A-1;{TAG};A;2026-09-01T12:00:00.1+02:00;1',
        f'B-1;{TAG};14;2026-09-01T12:00:00.2+02:00;1',
        'TTL1-1;00000001;15;2026-09-01T12:00:00.21+02:00;1',
        'TTL2-1;00000002;16;2026-09-01T12:00:00.22+02:00;1',
        'TTL1-1;00000001;17;2026-09-01T12:00:00.23+02:00;1',
    ])+'\n')
    settings = Settings([InputFile(str(source), board_id=1, profile='v1.5')], str(custom or root), str(main),
                        csv=True, parquet=True, interpolate=False, whitelist=False,
                        routes={'B-1':'device_antenna1'})
    return main, source, settings


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_clean_root_default_outputs_readme_and_all_five_types(tmp_path):
    main, raw, settings = experiment(tmp_path, 'Cohort.v2')
    before = raw.read_bytes()
    result = run(settings)
    names = {'Cohort.v2.'+g+'.'+ext for g in ('data','device_antenna1','TTL_data1') for ext in ('csv','parquet')}
    names |= {'Cohort.v2.'+g+'.json' for g in ('config','diagnostic','acquisition_gui_state')}
    names |= {README,'raw_antenna_reads'}
    assert {p.name for p in tmp_path.iterdir()} == names
    assert result['directory'] == str(tmp_path)
    assert result['diagnostic_path'] == str(tmp_path/'Cohort.v2.diagnostic.json')
    metadata = json.loads(main.read_text())
    assert links(metadata) == {MAIN_LINK:'Cohort.v2.acquisition_gui_state.json',LOG_LINK:'Cohort.v2.diagnostic.json'}
    assert metadata['acquisition']['active_inputs']['ttl'] == ['TTL1-1','TTL2-1']
    readme = (tmp_path/README).read_text()
    assert 'Inputs: TTL1-1, TTL2-1.' in readme
    for line in readme.splitlines():
        if ' — ' in line:
            assert (tmp_path/line.split(' — ',1)[0]).exists()
    for output in result['outputs']:
        assert output['file'] in readme
        assert (tmp_path/output['file']).suffix in ('.parquet','.csv')
        if output['file'].endswith('.parquet'):
            schema = pl.read_parquet_schema(tmp_path/output['file'])
            assert list(schema) == ['datetime','antenna','time_under','animal_id','internal_board_timestamp','inserted']
            assert schema['antenna'] == pl.Categorical()
            assert schema['time_under'] == pl.Duration('us')
            assert schema['internal_board_timestamp'] == pl.Datetime('us','Europe/Warsaw')
    assert raw.read_bytes() == before


def test_ttl_inventory_and_board_specific_split_then_regroup_retire(tmp_path):
    main, raw, settings = experiment(tmp_path)
    second = raw.with_name('board2.csv')
    second.write_text('TTL1-2;00000001;1;2026-09-01T12:00:00+02:00;2\n')
    settings.inputs.append(InputFile(str(second), board_id=2, profile='v1.5'))
    detected = inventory(settings.inputs, settings.timezone)
    assert detected['ttl_inputs'] == ['TTL1-1','TTL1-2','TTL2-1']
    settings.ttl_routes = {'TTL1-2':'TTL_data2', 'TTL2-1':'TTL_data2'}
    first = run(settings)
    files = {o['group']:o for o in first['outputs'] if o['file'].endswith('.parquet')}
    assert files['TTL_data1']['sources'] == ['TTL1-1']
    assert files['TTL_data2']['sources'] == ['TTL1-2','TTL2-1']
    frame = pl.read_parquet(tmp_path/files['TTL_data2']['file'])
    assert frame.height == 2 and frame['animal_id'].null_count() == 2
    assert frame['time_under'].dt.total_microseconds().to_list() == [0,0]
    settings.ttl_routes = {}
    settings.routes = {}
    settings.csv = False
    raw_hash = hashlib.sha256(raw.read_bytes()).hexdigest()
    (tmp_path/'personal_notes.txt').write_text('retain')
    second_result = run(settings)
    assert {o['file'] for o in second_result['outputs']} == {'E.data.parquet','E.TTL_data1.parquet'}
    assert not list(tmp_path.glob('*.csv'))
    assert not list(tmp_path.glob('*.device_antenna*'))
    assert not (tmp_path/'E.TTL_data2.parquet').exists()
    assert 'TTL_data2' not in (tmp_path/README).read_text()
    assert (tmp_path/'personal_notes.txt').read_text() == 'retain'
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == raw_hash
    loaded = load_config_bundle(main)
    assert loaded['acquisition']['preprocessing']['options']['ttl_routes'] == {}
    assert loaded['acquisition']['active_inputs'].get('auxiliary_rfid', []) == []


@pytest.mark.parametrize('routes', [{'TTL1-1':'main'},{'A-1':'TTL_data1'},{'TTL1-1':'TTL_data0'}])
def test_ttl_cannot_enter_rfid_groups(tmp_path, routes):
    _, _, settings = experiment(tmp_path)
    settings.ttl_routes = routes
    with pytest.raises(ValueError,match='TTL'): settings.validate()


def test_custom_output_keeps_config_and_diagnostic_at_root_and_links_relocate(tmp_path):
    root, output = tmp_path/'experiment', tmp_path/'results'
    main, _, settings = experiment(root, custom=output)
    result = run(settings)
    assert Path(result['diagnostic_path']).parent == root
    assert not list(output.glob('*.json'))
    assert pl.read_parquet_schema(output/'E.data.parquet')['antenna'] == pl.Categorical()
    import pyarrow.parquet as pq
    assert pq.read_schema(output/'E.data.parquet').metadata[b'diagnostics'] == b'../experiment/E.diagnostic.json'
    log = json.loads((root/'E.diagnostic.json').read_text())
    assert all((root/entry['relative_path']).exists() for entry in log['outputs'])
    moved = tmp_path/'moved'
    moved.mkdir()
    shutil.move(str(root), str(moved/root.name)); shutil.move(str(output), str(moved/output.name))
    main = moved/root.name/main.name
    assert load_config_bundle(main)['recording']['name'] == 'E'
    assert (main.parent/links(json.loads(main.read_text()))[LOG_LINK]).is_file()


def test_separate_parts_follow_three_component_names(tmp_path):
    _, raw, settings = experiment(tmp_path)
    second = raw.with_name('second.csv')
    second.write_text(raw.read_text().replace('12:00:', '13:00:'))
    settings.inputs.append(InputFile(str(second), board_id=1, profile='v1.5', group='2'))
    settings.split_restarts = True
    result = run(settings)
    assert {o['file'] for o in result['outputs']} == {f'E.{group}_part{part:02d}.{ext}' for group in ('data','device_antenna1','TTL_data1') for part in (1,2) for ext in ('csv','parquet')}


@pytest.mark.parametrize('failure', ['cancel','export','metadata_change','unowned_target','modified_output'])
def test_failed_reprocessing_preserves_completed_files(tmp_path, failure):
    main, _, settings = experiment(tmp_path)
    run(settings)
    if failure == 'unowned_target':
        (tmp_path/'E.device_antenna2.csv').write_text('unrelated')
        settings.routes = {'B-1':'device_antenna2'}
    if failure == 'modified_output':
        (tmp_path/'E.data.csv').write_text('manually edited')
    before = snapshot(tmp_path)
    if failure == 'cancel':
        with pytest.raises(Cancelled):run(settings, cancelled=lambda:True)
    elif failure == 'export':
        with patch('deepecohab_recorder.preprocessing.pipeline.export', side_effect=OSError('simulated disk failure')):
            with pytest.raises(ValueError,match='disk failure'):run(settings)
    elif failure == 'metadata_change':
        def progress(percent, text):
            if percent == 60:
                document = json.loads(main.read_text());document['recording']['notes']='External edit'
                main.write_text(json.dumps(document))
                before[main.name] = main.read_bytes()
        with pytest.raises(ValueError,match='metadata changed'):run(settings, progress=progress)
    else:
        with pytest.raises(ValueError):run(settings)
    assert snapshot(tmp_path) == before
    assert not any(p.name.startswith('.') for p in tmp_path.iterdir())


def test_commit_failure_rolls_back_all_files(tmp_path):
    main, _, settings = experiment(tmp_path)
    run(settings)
    before = snapshot(tmp_path)
    real_replace = os.replace
    failed = False
    def fail_once(src, dst):
        nonlocal failed
        if not failed and Path(dst) == main and '.processing-' in str(src):
            failed = True
            raise PermissionError('simulated Windows open-file lock')
        return real_replace(src,dst)
    settings.routes = {}
    with patch('deepecohab_recorder.preprocessing.publication.os.replace', side_effect=fail_once):
        with pytest.raises(ValueError,match='open-file lock'):run(settings)
    assert failed and snapshot(tmp_path) == before
    assert not any(p.name.startswith('.') for p in tmp_path.iterdir())


@pytest.mark.parametrize('committed',[False,True])
def test_process_crash_recovered_before_config_discovery(tmp_path, committed):
    main, _, settings = experiment(tmp_path)
    run(settings)
    old = main.read_bytes()
    backup = main.with_name('.'+main.name+'.previous-test')
    os.replace(main,backup)
    if committed:
        changed = json.loads(old);changed['recording']['notes']='committed change'
        main.write_text(json.dumps(changed))
    journal = tmp_path/'.E.preprocessing.pending'
    journal.write_text(json.dumps(dict(experiment='E',output=str(tmp_path),status='committed' if committed else 'prepared',
        files=[dict(target=str(main),backup=str(backup),existed=True)])))
    assert discover_config(tmp_path,'E') == main
    assert (main.read_bytes() == old) is not committed
    assert not backup.exists() and not journal.exists()


def test_publication_lock_rejects_second_job(tmp_path):
    with Publication(tmp_path,tmp_path,'E'):
        with pytest.raises(ValueError,match='already being preprocessed'):
            with Publication(tmp_path,tmp_path,'E'):pass
    assert list(tmp_path.iterdir()) == []


def test_old_nested_config_load_then_save_and_legacy_journals_import(tmp_path):
    cfg=valid_config();cfg['recording']['name']='Old'
    path=tmp_path/'Old.json'
    main,state=project(cfg,path)
    main['acquisition']['_provenance'][MAIN_LINK]='acquisition/acquisition_gui_state.json'
    (tmp_path/'acquisition').mkdir();(tmp_path/'acquisition/acquisition_gui_state.json').write_text(json.dumps(state))
    path.write_text(json.dumps(main))
    journal=tmp_path/'session_configs/run_old';journal.mkdir(parents=True)
    run_meta={'devices':{'board1':{'config':{'board_id':1,'com_port':'COM5','hardware':'DeepEcoHAB RFID DAQ v1.5'},
        'start_datetime':'2026-09-01T12:00:00+02:00','end_datetime':None,'data_file':'capture.csv'}}}
    (journal/'run.json').write_text(json.dumps(run_meta))
    before=snapshot(tmp_path)
    repo=ConfigRepository(lambda:'Old',lambda:str(tmp_path));repo.import_file(path)
    assert snapshot(tmp_path)==before
    state_path,new_main=repo.save()
    assert new_main.name=='Old.config.json' and state_path.name=='Old.acquisition_gui_state.json'
    assert not path.exists() and not (tmp_path/'acquisition/acquisition_gui_state.json').exists()
    loaded=load_config_bundle(new_main)
    assert len(loaded['acquisition']['recording_history']['runs'])==1
    periods=acquisition_journals(tmp_path,'Europe/Warsaw',loaded)
    assert periods
    shutil.rmtree(tmp_path/'session_configs')
    assert acquisition_journals(tmp_path,'Europe/Warsaw',load_config_bundle(new_main)) == periods
    assert not list(tmp_path.glob('*metadata*'))


def test_multiple_experiments_do_not_share_state_or_data(tmp_path):
    main,raw,settings=experiment(tmp_path,'One');run(settings)
    first=(tmp_path/'One.data.parquet').read_bytes()
    cfg=load_config_bundle(main);cfg['recording']['name']='Two'
    second=tmp_path/'Two.config.json';save_config_bundle(second,cfg)
    settings.config_path=str(second);run(settings)
    assert (tmp_path/'One.data.parquet').read_bytes()==first
    assert load_config_bundle(main)['recording']['name']=='One'
    assert load_config_bundle(second)['recording']['name']=='Two'
    assert all(name in (tmp_path/README).read_text() for name in ('One.data.parquet','Two.data.parquet'))


def test_grouping_ui_ttl_choices_cancel_and_saved_restoration(gui):
    from deepecohab_recorder.ui.preprocessing_outputs import OutputGroupsDialog
    tab=gui.prepare_inputs()
    tab._used_antennas={'A-1','B-1'}
    tab._used_ttl={'TTL1-1','TTL2-1','TTL1-2'}
    original=copy.deepcopy(tab.options)
    dialog=OutputGroupsDialog(tab)
    assert dialog.ttl_values()=={}
    assert dialog.ttl.default=='TTL_data1'
    assert all(dialog.ttl.table.cellWidget(i,2).findData('main') == -1 for i in range(3))
    dialog.ttl.table.selectAll();dialog.ttl.separate()
    assert set(dialog.ttl_values().values())=={'TTL_data2','TTL_data3','TTL_data4'}
    dialog.rfid.table.selectAll();dialog.rfid.group_together()
    assert set(dialog.values().values())=={'device_antenna1'}
    dialog.reject();assert tab.options==original
    dialog=OutputGroupsDialog(tab)
    dialog.ttl.table.selectAll();dialog.ttl.separate()
    tab.options['ttl_routes']=dialog.ttl_values();dialog.accept()
    gui.window.save_section(tab)
    saved=load_config_bundle(gui.root/'Test.config.json')
    assert saved['acquisition']['preprocessing']['options']['ttl_routes']==tab.options['ttl_routes']


def test_malformed_samples_are_bounded_and_no_serial_duplicate(tmp_path):
    samples=[]
    collector=MalformedSamples(samples)
    for i in range(100):collector.write(json.dumps({'data_b64':'A'*20000,'error':str(i)}))
    assert len(samples)==20 and all(len(v['data_b64'])==4096 and v['sample_truncated'] for v in samples)
    device_state(tmp_path,'E','camera_test',{'value':1})
    assert {p.name for p in tmp_path.iterdir()}=={'E.acquisition_gui_state.json'}


def test_compact_history_survives_abrupt_exit_and_supplies_preprocessing_bounds(tmp_path):
    from types import SimpleNamespace
    from deepecohab_recorder.services.session import RunSession
    from deepecohab_recorder.clock import ExperimentClock
    main,raw,settings=experiment(tmp_path)
    repo=ConfigRepository(lambda:'E',lambda:str(tmp_path));repo.import_file(main)
    session=RunSession(repo)
    controller=SimpleNamespace(clock=ExperimentClock('Europe/Warsaw'),started_iso='2026-09-01T11:59:50+02:00',
        stopped_iso='',last_error='',data_file=raw,lines_ok=6,lines_written=6,lines_bad=0,lines_dropped=0)
    session.board_started('board1',controller,dict(board_id=1,com_port='COM5',hardware='DeepEcoHAB RFID DAQ v1.5'))
    # A fresh repository represents restart after a process/power loss: no stop was received.
    fresh=ConfigRepository(lambda:'E',lambda:str(tmp_path));loaded=fresh.import_file(main)
    evidence,warnings=acquisition_journals(tmp_path,'Europe/Warsaw',loaded)
    assert len(evidence)==1 and not warnings
    assert evidence[0]['recording_end'] is None and evidence[0]['recording_start']==controller.started_iso
    assert evidence[0]['data_file']=='raw_antenna_reads/capture.csv'
    specs=scan([str(raw)],loaded,'Europe/Warsaw')
    assert specs[0]['input'].start==controller.started_iso
    result=run(settings)
    timeline=load_config_bundle(main)['recording']['timeline']
    assert timeline['start_datetime']==controller.started_iso
    assert timeline['end_datetime'].startswith('2026-09-01T12:00:00.200')
    log=json.loads(Path(result['diagnostic_path']).read_text())
    assert log['acquisition_evidence'][0]['true_stop_unknown'] is True
    assert 'recording_end' not in log['acquisition_evidence'][0]
    assert not any((tmp_path/f).exists() for f in ('session_configs','diagnostic_logs','acquisition'))


def test_runtime_writes_preserve_external_gui_state_edits_and_detect_conflict(tmp_path):
    main,_,_=experiment(tmp_path)
    repo=ConfigRepository(lambda:'E',lambda:str(tmp_path));repo.import_file(main)
    state_path=tmp_path/'E.acquisition_gui_state.json'
    state=json.loads(state_path.read_text());state['state']['custom_external']='retain me'
    state_path.write_text(json.dumps(state))
    repo.update_paths({'acquisition.recording_history':{'runs':[],'events':[{'event':'test'}]}})
    repo.persist_runtime()
    assert json.loads(state_path.read_text())['state']['custom_external']=='retain me'
    with pytest.raises(ValueError,match='changed'):repo.save()


def test_new_state_collision_is_not_overwritten(tmp_path):
    cfg=valid_config();cfg['recording']['name']='E'
    path=tmp_path/'E.acquisition_gui_state.json';path.write_text('{"user_document": true}')
    before=path.read_bytes()
    with pytest.raises(ValueError):save_config_bundle(tmp_path/'E.config.json',cfg)
    assert path.read_bytes()==before
