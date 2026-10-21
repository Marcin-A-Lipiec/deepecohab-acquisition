"""Patch 16 public contracts and scientific safety regressions."""
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
import pytest
import polars as pl

from deepecohab_recorder.config_bundle import load_config_bundle, save_config_bundle, project
from deepecohab_recorder.device_antennas import resolve_routes, device_errors
from deepecohab_recorder.schema import migrate_config, validate_config
from deepecohab_recorder.preprocessing.diagnostics import Diagnostics
from deepecohab_recorder.preprocessing.interpolation import PhysicalGraph
from deepecohab_recorder.preprocessing.compact_diagnostics import recording_summary
from deepecohab_recorder.preprocessing.models import InputFile, Settings
from deepecohab_recorder.preprocessing.pipeline import run
from deepecohab_recorder.preprocessing.sources import SourceGroup
from tests.helpers import valid_config
from tests import test_preprocessing as previous
TAG = previous.TAG
from tests.test_diagnostics_and_options import gui


def test_metadata_only_end_choice_nullable_animals_and_length(tmp_path):
    cfg = valid_config()
    animal = cfg['recording']['cohort']['animals'][0]
    animal.update(subject_name='  ', treatment='', notes='')
    cfg['recording']['timeline']['end_with'] = 'light_phase'
    cfg['recording']['layout']['tunnels'][0]['length'] = 20.5
    before = copy.deepcopy(cfg['recording']['timeline'])
    path = tmp_path/'Test.config.json'
    save_config_bundle(path, cfg)
    main = json.loads(path.read_text())
    assert main['version'] == 8
    assert main['recording']['timeline']['end_with'] == 'light_phase'
    for key in ('start_datetime','end_datetime','start_from','phases'):
        assert main['recording']['timeline'][key] == before[key]
    result = main['recording']['cohort']['animals'][0]
    assert result['subject_name'] is None and result['treatment'] is None and result['notes'] == ''
    assert main['recording']['layout']['tunnels'][0]['length'] == 20.5
    assert 'antenna_distances_cm' not in main['recording']['layout']['tunnels'][0]
    loaded = load_config_bundle(path)
    assert loaded['recording']['layout']['tunnels'][0]['length'] == 20.5
    import jsonschema
    jsonschema.validate(main, json.loads((Path(__file__).parents[1]/'docs/config_v8.schema.json').read_text()))
    for field in ('mouse_line','genotype','genetic_background','date_of_birth','sex','tag'):
        invalid = copy.deepcopy(cfg)
        invalid['recording']['cohort']['animals'][0][field] = ''
        draft, _ = project(invalid, path)
        assert draft['recording']['cohort']['animals'][0][field] == ''
        assert validate_config(invalid)
        jsonschema.validate(draft, json.loads((Path(__file__).parents[1]/'docs/config_v8.schema.json').read_text()))
    cfg['recording']['cohort']['animals'][0]['sex'] = 'Unknown'
    assert project(cfg,path)[0]['recording']['cohort']['animals'][0]['sex'] == 'Unknown'
    assert validate_config(cfg)


def device_config():
    cfg = valid_config()
    cfg['recording']['devices'] = [
        dict(name='Lickometer', description='water', antenna='I-1', position='cage_1', TTL_port='TTL1-1'),
        dict(name=' LICKOMETER ', description='sucrose', antenna='J-1', position='cage_1', TTL_port=None)]
    return migrate_config(cfg)


def test_device_groups_follow_names_with_stable_overrides_and_no_collisions():
    cfg = device_config()
    options = {}
    rfid, ttl, names, _ = resolve_routes(cfg, options)
    assert rfid == {'I-1':'device_antenna1','J-1':'device_antenna1'}
    assert ttl == {'TTL1-1':'device_antenna1'}
    cfg['recording']['devices'][1]['device_type'] = 'Sensor'
    rfid, ttl, _, _ = resolve_routes(cfg, options)
    assert rfid['I-1'] == 'device_antenna1' and rfid['J-1'] == 'device_antenna2'
    options.update(routes={'I-1':'main','A-1':'custom'}, ttl_routes={'TTL1-1':'TTL_data2'})
    rfid, ttl, _, _ = resolve_routes(cfg, options)
    assert rfid['I-1'] == 'main' and rfid['A-1'] == 'device_antenna3'
    assert ttl['TTL1-1'] == 'TTL_data2'
    options['routes'].clear(); options['ttl_routes'].clear()
    assert resolve_routes(cfg, options)[1]['TTL1-1'] == 'device_antenna1'
    cfg['recording']['devices'][1]['TTL_port'] = 'TTL1-1'
    with pytest.raises(ValueError, match='explicit destination'): resolve_routes(cfg, options)
    options['ttl_routes']['TTL1-1'] = 'TTL_data1'
    assert resolve_routes(cfg, options)[1]['TTL1-1'] == 'TTL_data1'


def test_device_save_separate_from_topology_shared_parquet_and_decoupling(tmp_path):
    cfg = device_config()
    assert device_errors(cfg['recording']) == []
    cfg['recording']['devices'][0]['_editor_position'] = [9, 14]
    main = tmp_path/'Test.config.json'
    save_config_bundle(main, cfg)
    visible = json.loads(main.read_text())['recording']
    assert list(visible)[-1] == 'notes'
    assert list(visible).index('devices') == list(visible).index('events') + 1
    assert all('I-1' not in t['antennas'] for t in visible['layout']['tunnels'])
    assert '_editor_position' not in visible['devices'][0]
    assert load_config_bundle(main)['recording']['devices'][0]['_editor_position'] == [9,14]
    tag=cfg['recording']['cohort']['animals'][0]['tag']
    raw=tmp_path/'raw.csv'
    raw.write_text('\n'.join(f'{ant};{tag if not ant.startswith("TTL") else "00000001"};{i:X};2026-09-01T12:00:00.{i:02}0+02:00;1' for i,ant in enumerate(('A-1','I-1','J-1','TTL1-1','TTL2-1')))+'\n')
    settings=Settings([InputFile(str(raw), board_id=1, profile='v1.5')], str(tmp_path), str(main), interpolate=False)
    before=settings.to_dict()
    result=run(settings)
    assert settings.to_dict() == before  # derived defaults must not turn into overrides
    frame=pl.read_parquet(tmp_path/'Test.device_antenna1.parquet')
    assert set(frame['antenna'].cast(pl.String)) == {'I-1','J-1','TTL1-1'}
    ttl=frame.filter(pl.col('antenna')=='TTL1-1')
    assert ttl['animal_id'].null_count()==1 and ttl['time_under'].dt.total_microseconds()[0]==0
    assert frame.schema['antenna']==pl.Categorical()
    assert list(frame.schema)==['datetime','antenna','time_under','animal_id','internal_board_timestamp','inserted']
    log=json.loads(Path(result['diagnostic_path']).read_text())
    assert len(log['transition_diagnostic'])==8
    assert all(r['antenna'] not in ('I-1','J-1') for r in log['transition_diagnostic'])
    assert len(log['device_diagnostic'])==2
    assert 'Device: Lickometer-cage_1.' in (tmp_path/'DATATYPE READ ME.txt').read_text()
    settings.ttl_routes={'TTL1-1':'TTL_data2'}
    run(settings)
    assert 'TTL1-1' not in set(pl.read_parquet(tmp_path/'Test.device_antenna1.parquet')['antenna'].cast(pl.String))
    assert pl.read_parquet(tmp_path/'Test.TTL_data2.parquet').height==1
    assert load_config_bundle(main)['acquisition']['preprocessing']['options']['routes']=={}


def test_ambiguous_and_multi_step_rejected_candidates_and_zero_matrix(tmp_path):
    g=PhysicalGraph({}, {1})
    g.movement_antennas={'A-1','B-1','C-1','D-1'}
    g.movement_edges={'A-1':{'B-1','C-1'},'B-1':{'A-1','D-1'},'C-1':{'A-1','D-1'},'D-1':{'B-1','C-1'}}
    assert g.candidate_antennas('A-1','D-1')==('B-1','C-1')
    d=Diagnostics(tmp_path,'Europe/Warsaw')
    d.reject(TAG, ['B-1','B-1','C-1'], 'ambiguous_route')
    assert d.rejected[('B-1',TAG)]==1 and sum(d.rejection_reasons.values())==2
    d._inserts.close()
    e=previous.PreprocessingTests(); e.setUp()
    try:
        spec=e.raw('failed.txt', [(0,'A',TAG),(1000,'C',TAG),(2000,'A','FFFFA819')])
        cfg=e.root/'E.config.json';cfg.write_text(json.dumps(e.cfg))
        result=run(Settings([spec],str(e.root),str(cfg),parquet=False,csv=True,interpolate=True))
        report=json.loads(Path(result['diagnostic_path']).read_text())
        assert len(report['transition_diagnostic'])==8
        row=next(r for r in report['transition_diagnostic'] if (r['antenna'],r['animal'])==('B-1',TAG))
        assert row==dict(antenna='B-1',animal=TAG,correct=0,interpolated=0,rejected=1)
        assert report['counts']['rejected_tag_reads']==1
        assert report['counts']['rejected_interpolation_candidates']==1
        assert report['definitions']['rejected']
    finally:e.tearDown()


def test_folder_1000_files_one_recording_but_clocks_and_confirmed_stop_retained(tmp_path):
    folder=tmp_path/'raw';folder.mkdir()
    sources=[]
    for i in range(1000):
        sources.append(dict(id=i+1,path=str(folder/f'{i}.txt'),source_path=str(folder),source_kind='folder',
                            recording_part=1,board_id=1,bytes=20,sha256=f'{i:064x}',first_pc_us=i*1000000000,
                            last_pc_us=i*1000000000+1,valid_reads=1,profile='legacy_raw'))
    info=dict(files=sources, clock_epochs=[dict(epoch=1,files=[1,999]),dict(epoch=2,files=[1000])],
              recording_coverage=[dict(board_id=1,confirmed_interruptions=[dict(start='2026-09-01T12:00:00+02:00',end='2026-09-01T12:00:02+02:00',duration_seconds=2)])])
    rows,gaps,clocks,_=recording_summary(info,tmp_path,'Europe/Warsaw',{})
    assert len(rows)==1 and rows[0]['file_count']==1000 and rows[0]['path']=='raw'
    assert len(clocks)==2 and clocks[0]['recordings']==[1]
    assert len(gaps)==1 and gaps[0]['kind']=='confirmed_DAQ_inactive'
    assert '.txt' not in json.dumps(rows)
    changed=copy.deepcopy(info);changed['files'][0]['sha256']='f'*64
    assert recording_summary(changed,tmp_path,'Europe/Warsaw',{})[0] == rows
    assert 'sha256' not in rows[0]


def test_ui_device_visibility_presets_and_dynamic_group_defaults(gui):
    from PySide6.QtWidgets import QComboBox, QStyleOptionViewItem
    from deepecohab_recorder.ui.antenna_assignment import AntennaAssignmentDialog
    from deepecohab_recorder.ui.preprocessing_outputs import OutputGroupsDialog
    from deepecohab_recorder.ui.animal_presets import GENOTYPES,BACKGROUNDS
    w=gui.window
    cfg=device_config();w.services.repo.replace(cfg);w._apply(cfg)
    ui=w.widgets['layout'].ui
    dialog=AntennaAssignmentDialog(ui,'I-1',w,device=cfg['recording']['devices'][0],layout=cfg['recording']['layout'])
    dialog.show();gui.app.processEvents()
    assert dialog.device_panel.isVisible() and dialog.link_ttl.isChecked()
    dialog.role.setCurrentIndex(0)
    assert not dialog.device_panel.isVisible()
    dialog.role.setCurrentIndex(1)
    assert dialog.device_value()['TTL_port']=='TTL1-1'
    dialog.close()
    layout=w.widgets['layout'];layout.draw()
    assert 'device:I-1' in layout._items
    assert len(layout.read()['recording.devices'])==2
    tab=w.widgets['preprocessing'];tab.config_path.clear()
    groups=OutputGroupsDialog(tab)
    assert groups.rfid.defaults['I-1']==groups.ttl.defaults['TTL1-1']
    row=groups.ttl.sources.index('TTL1-1');groups.ttl.table.selectRow(row);groups.ttl.assign('TTL_data2')
    assert groups.ttl_values()=={'TTL1-1':'TTL_data2'}
    groups.reset_all();assert groups.values()==groups.ttl_values()=={}
    groups.close()
    assert len(BACKGROUNDS)==10 and list(GENOTYPES)==['KO','WT','HET','MUT']
    animals=w.widgets['animals'].table
    for column,editable in ((4,True),(5,True),(7,False)):
        index=animals.model().index(0,column)
        editor=animals.itemDelegateForColumn(column).createEditor(animals,QStyleOptionViewItem(),index)
        assert isinstance(editor,QComboBox) and editor.isEditable() is editable
        if column==7:assert [editor.itemText(i) for i in range(editor.count())]==['Male','Female']
        editor.deleteLater()


def test_layout_main_to_device_and_back_preserves_identity(gui):
    from PySide6.QtWidgets import QDialog
    from deepecohab_recorder.ui.antenna_assignment import AntennaAssignmentDialog
    tab=gui.window.widgets['layout'];tunnel=tab.graph['tunnels'][0];source=tunnel['antennas'][0]
    def as_device(dialog):
        dialog.role.setCurrentIndex(1);dialog.device_type.setText('Sensor');dialog.device_position.setCurrentIndex(0)
        return QDialog.Accepted
    with patch.object(AntennaAssignmentDialog,'exec',as_device):tab.assign_antenna(tunnel,0)
    tab.draw();data=tab.read();device=data['recording.devices'][0]
    assert device['antenna']==source and device['TTL_port'] is None
    assert all(source not in t['antennas'] for t in data['recording.layout']['tunnels'])
    assert all(source not in c['antennas'] for c in data['recording.layout']['cages'])
    def as_main(dialog):
        dialog.role.setCurrentIndex(0);assert dialog.main_slot.currentData() is not None
        return QDialog.Accepted
    with patch.object(AntennaAssignmentDialog,'exec',as_main):tab.edit_device(tab.devices[0])
    tab.draw();data=tab.read()
    assert data['recording.devices']==[]
    assert sum(source in t['antennas'] for t in data['recording.layout']['tunnels'])==1


def test_v5_pair_and_old_owned_auxiliary_outputs_migrate_safely(tmp_path):
    from tests.test_output_publication import experiment
    main,raw,settings=experiment(tmp_path)
    result=run(settings);log_path=Path(result['diagnostic_path']);log=json.loads(log_path.read_text())
    for output in log['outputs']:
        output['sha256']=hashlib.sha256((tmp_path/output['file']).read_bytes()).hexdigest()
        if 'device_antenna' in output['file']:
            current=tmp_path/output['file'];legacy=output['file'].replace('device_antenna','auxiliary_data');current.rename(tmp_path/legacy)
            output['file']=legacy;output['relative_path']=legacy;output['group']=output['group'].replace('device_antenna','auxiliary_data')
    log['diagnostics_version']=10;log_path.write_text(json.dumps(log))
    before=raw.read_bytes();(tmp_path/'my_notes.txt').write_text('retain')
    document=json.loads(main.read_text());document['recording']['timeline']['datetime_provenance']=document['acquisition'].pop('datetime_provenance');document['version']=5;document['recording'].pop('devices');document['recording']['timeline'].pop('end_with')
    main.write_text(json.dumps(document));loaded=load_config_bundle(main)
    assert loaded['version']==8 and loaded['recording']['timeline']['end_with']=='dark_phase'
    run(settings)
    assert not list(tmp_path.glob('*.auxiliary_data*'))
    assert list(tmp_path.glob('*.device_antenna1.parquet'))
    assert (tmp_path/'my_notes.txt').read_text()=='retain' and raw.read_bytes()==before


def test_partial_extended_repair_accounts_for_each_missing_antenna():
    e=previous.PreprocessingTests();e.setUp()
    try:
        events=[r for r in e.training_events()[:24] if r[1]!='D']+[(3000,'A',TAG),(3050,'A',TAG),(4000,'D',TAG)]
        e.process([e.raw('partial.txt',events)],interpolate=True,interpolation_mode='extended')
        report=e.summary
        assert any(r['antenna']=='B-1' and r['interpolated']>0 for r in report['transition_diagnostic'])
        assert any(r['antenna']=='C-1' and r['rejected']>0 for r in report['transition_diagnostic'])
        assert sum(r['rejected'] for r in report['transition_diagnostic'])==report['counts']['rejected_interpolation_candidates']
        assert sum(report['issues']['rejected_interpolation_reasons'].values())==report['counts']['rejected_interpolation_candidates']
    finally:e.tearDown()
