"""Patch 12 contracts: logical sources, canonical pairs, immutable cycles, evidence."""
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest

from deepecohab_recorder.clock import establish_schedule, fixed_phase_state, fixed_phase_origin, validate_schedule
from deepecohab_recorder.config import ConfigRepository
from deepecohab_recorder.config_bundle import (load_config_bundle, save_config_bundle, bundle_digest, project,
    resolve_link, links, MAIN_LINK, LOG_LINK, backup_bundle, _journal)
from deepecohab_recorder.event_metadata import storage_config
from deepecohab_recorder.experiment_timing import select_timing
from deepecohab_recorder.metadata_updates import write_config_update
from deepecohab_recorder.preprocessing.layout_check import check_layout, apply_layout_changes, antenna_labels
from deepecohab_recorder.preprocessing.models import InputFile, Settings
from deepecohab_recorder.preprocessing.pipeline import run
from deepecohab_recorder.preprocessing.readers import records
from deepecohab_recorder.preprocessing.recording_gaps import recording_coverage
from deepecohab_recorder.schema import default_config, migrate_config
from deepecohab_recorder.source_identity import source_id, canonical_source, IdentityConflict
from deepecohab_recorder.utils import atomic_write_json, source_channel_id
from tests.helpers import valid_config


@pytest.mark.parametrize('channel,board,port,ttl,expected', [('1',1,'COM5',False,'1-1'),('8',2,'COM42',False,'8-2'),('A',1,'',False,'A-1'),('2',2,'COM3',True,'TTL2-2')])
def test_recording_names_use_board_not_usb_port(channel,board,port,ttl,expected):
    assert source_channel_id(channel,board,port,ttl)==expected
    assert source_channel_id(channel,board,'COM99',ttl)==expected


def test_com_migration_uses_mapping_not_port_number():
    cfg=valid_config()
    cfg['acquisition']['boards']['board_1']['com_port']='COM42'
    cfg['recording']['layout']['tunnels'][0]['antennas']=['ACOM42','BCOM42']
    cfg['acquisition']['preprocessing']['options']={'routes':{'ACOM42':'licks'},'aliases':{'ACOM42':'spoutCOM42'}}
    migrated=migrate_config(cfg)
    assert migrated['recording']['layout']['tunnels'][0]['antennas']==['A-1','B-1']
    assert migrated['acquisition']['preprocessing']['options']['routes']=={'A-1':'device_antenna1'}
    assert migrated['acquisition']['preprocessing']['options']['aliases']=={'A-1':'spout-1'}
    assert canonical_source('A_2_COM42')=='A-2'
    assert canonical_source('ACOM42')=='ACOM42'
    assert canonical_source('A')=='A'


def test_bare_channels_need_confirmation_and_shared_channels_never_guessed():
    cfg=valid_config()
    cfg['recording']['layout']['tunnels'][0]['antennas']=['1','2']
    one=[InputFile('not-read',board_id=2,profile='legacy_raw')]
    report=check_layout(cfg,one)
    assert report['changes']=={'1':'1-2','2':'2-2'}
    report=check_layout(cfg,[*one,InputFile('not-read2',board_id=1,profile='legacy_raw')])
    assert report['ambiguous']['1']==['1','2']
    assert '1' not in report['changes']


def test_identity_mismatch_is_not_a_malformed_row_to_discard(tmp_path):
    p=tmp_path/'raw.txt'
    p.write_text('A-1;0000A819;1;2026-09-01T12:00:00+02:00;1\nA-2;0000A819;2;2026-09-01T12:00:00.01+02:00;1\n')
    stream=records(InputFile(str(p)), 'Europe/Warsaw')
    assert next(stream)[0].antenna=='A-1'
    with pytest.raises(IdentityConflict):list(stream)


def test_same_board_can_change_connection_between_runs(tmp_path):
    specs=[]
    for i,port in enumerate(('COM5','COM9')):
        p=tmp_path/f'part{i}.txt';p.write_text(f'A{port};0000A819;1;2026-09-01T{12+i}:00:00+02:00;1\n')
        specs.append(InputFile(str(p),port,1,part=i+1))
    settings=Settings(specs,str(tmp_path/'out'),whitelist=False,interpolate=False,csv=True,parquet=False)
    result=run(settings)
    log=json.loads(Path(result['diagnostic_path']).read_text())
    assert {r['board'] for r in log['recordings']}=={1}
    assert {r['antenna'] for r in log['read_diagnostic']}=={'A-1'}
    assert len(log['clocks'])==2


def make_pair(tmp_path):
    cfg=valid_config();cfg['recording']['name']='Pair'
    cfg['recording']['project_name']='Tsc2'
    cfg['custom_setup']={'future_option':42}
    cfg['recording']['cohort']['animals'][0]['rack']='Z'
    cfg['recording']['layout']['junctions']=[{'id':'J1','parent_tunnel':'tunnel_1','position':0.5,'display_color':'red'}]
    cfg['recording']['events']=[{'name':'Handling','description':'Manual intervention','bouts':[{'start':'2026-09-01T13:00:00+02:00','end':None,'position':None,'end_user_specified':False}]}]
    cfg['acquisition']['preprocessing']={'sources':[],'options':{'window_ms':315,'routes':{'A-1':'licks'}}}
    path=tmp_path/'Pair.config.json'
    save_config_bundle(path,cfg)
    return path,cfg


def test_canonical_schema_has_only_agreed_metadata_and_preserves_state(tmp_path):
    import jsonschema
    path,cfg=make_pair(tmp_path)
    main=json.loads(path.read_text());secondary=resolve_link(path,links(main)[MAIN_LINK])
    jsonschema.validate(main,json.loads((Path(__file__).parents[1]/'docs/config_v8.schema.json').read_text()), format_checker=jsonschema.FormatChecker())
    jsonschema.validate(json.loads(secondary.read_text()),json.loads((Path(__file__).parents[1]/'docs/acquisition_gui_state_v1.schema.json').read_text()))
    assert set(tmp_path.glob('*.json')) == {path, secondary}
    t=main['recording']['timeline']
    for key in ('analysis_start_datetime','phase_schedule','datetime_mode','automatic_start_datetime','coverage','experiment_id'):
        assert key not in t
    assert set(main['acquisition'])=={'active_inputs','datetime_provenance','_provenance'}
    assert 'editor' not in main['recording']['layout']
    assert not any('interp' in k for k in main['recording']['layout'])
    loaded=load_config_bundle(path)
    assert loaded['custom_setup']==cfg['custom_setup']
    assert loaded['recording']['cohort']['animals'][0]['rack']=='Z'
    assert loaded['recording']['layout']['editor']==cfg['recording']['layout']['editor']
    assert loaded['recording']['layout']['junctions'][0]['display_color']=='red'
    assert not loaded['recording']['events']['bouts'][0]['end_user_specified']
    assert loaded['acquisition']['preprocessing']['options']['window_ms']==cfg['acquisition']['preprocessing']['options']['window_ms']


def test_relocation_and_missing_state_retain_science_without_enabling_devices(tmp_path):
    folder=tmp_path/'old';folder.mkdir();path,_=make_pair(folder)
    moved=tmp_path/'new';shutil.copytree(folder,moved)
    loaded=load_config_bundle(moved/path.name)
    assert loaded['acquisition']['preprocessing']['options']['window_ms']==315
    main=json.loads(path.read_text()); resolve_link(path,links(main)[MAIN_LINK]).unlink()
    notices=[];loaded=load_config_bundle(path,notices)
    assert notices and all(not b['active'] for b in loaded['acquisition']['boards'].values())
    assert loaded['recording']['project_name']=='Tsc2'


def test_external_main_edits_win_and_auxiliary_fields_survive(tmp_path):
    path,_=make_pair(tmp_path);main=json.loads(path.read_text())
    main['recording']['project_name']='New project'
    main['recording']['cohort']['animals'][0]['subject_name']='Renamed'
    main['recording']['timeline']['start_datetime']='2026-09-01T13:30:00+02:00'
    path.write_text(json.dumps(main))
    loaded=load_config_bundle(path)
    assert loaded['recording']['project_name']=='New project'
    assert loaded['recording']['cohort']['animals'][0]['rack']=='Z'
    assert select_timing(loaded['recording']['timeline'])['start_datetime']=='2026-09-01T13:30:00+02:00'
    assert loaded['recording']['timeline']['datetime_provenance']['start_source']=='external_config_edit'


@pytest.mark.parametrize('target',['main','secondary'])
def test_external_edits_block_worker_overwrite(tmp_path,target):
    path,_=make_pair(tmp_path);digest=bundle_digest(path);cfg=load_config_bundle(path)
    file=path if target=='main' else resolve_link(path,links(json.loads(path.read_text()))[MAIN_LINK])
    file.write_text(file.read_text()+'\n')
    before={p:p.read_bytes() for p in (path,resolve_link(path,links(json.loads(path.read_text()))[MAIN_LINK]))}
    with pytest.raises(ValueError,match='changed'):write_config_update(path,cfg,digest)
    assert all(p.read_bytes()==data for p,data in before.items())


def test_interrupted_pair_save_rolls_back_both_files(tmp_path):
    import deepecohab_recorder.config_bundle as bundle
    path,_=make_pair(tmp_path);cfg=load_config_bundle(path)
    secondary=resolve_link(path,links(json.loads(path.read_text()))[MAIN_LINK])
    before_main=json.loads(path.read_text());before_secondary=json.loads(secondary.read_text())
    original=bundle.atomic_write_json
    count=0
    def fail_once(file,value):
        nonlocal count
        count+=1
        if count==3:raise OSError('simulated main replacement failure')
        return original(file,value)
    cfg['recording']['notes']='must roll back'
    with patch.object(bundle,'atomic_write_json',side_effect=fail_once),pytest.raises(OSError):
        save_config_bundle(path,cfg)
    assert json.loads(path.read_text())==before_main and json.loads(secondary.read_text())==before_secondary
    assert not _journal(path).exists()


def test_crash_journal_is_recovered_on_next_load(tmp_path):
    path,_=make_pair(tmp_path);main=json.loads(path.read_text()); secondary=resolve_link(path,links(main)[MAIN_LINK]); old= json.loads(secondary.read_text())
    atomic_write_json(_journal(path),{'main':{'path':path.name,'old':main},'secondary':{'path':links(main)[MAIN_LINK],'old':old}})
    secondary.write_text('{}')
    assert load_config_bundle(path)['acquisition']['preprocessing']['options']['window_ms']==315
    assert not _journal(path).exists()


def test_backup_is_self_contained_and_preserves_original_bytes(tmp_path):
    path,_=make_pair(tmp_path);before=path.read_bytes()
    assert backup_bundle(path) is None
    assert path.read_bytes() == before
    assert not (tmp_path / 'session_configs').exists()
    assert load_config_bundle(path)['acquisition']['preprocessing']['options']['window_ms']==315


@pytest.mark.parametrize('start,probe',[('2026-03-28T12:00:00+01:00','2026-03-30T12:30:00+02:00'),('2026-10-24T12:00:00+02:00','2026-10-26T11:30:00+01:00')])
def test_fixed_cycles_cross_dst_and_main_alone_reconstructs_them(tmp_path,start,probe):
    cfg=valid_config();t=cfg['recording']['timeline']
    t.update(start_datetime=start,datetime_mode='automatic',phases={'light_phase':'00:00','dark_phase':'12:00'})
    t=establish_schedule(t,start);t=select_timing(t);cfg['recording']['timeline']=t
    path=tmp_path/'dst.json';save_config_bundle(path,cfg)
    main=json.loads(path.read_text())['recording']['timeline'];reconstructed=establish_schedule(main,main['start_datetime'])['phase_schedule']
    expected=fixed_phase_state(t['phase_schedule'],probe)
    assert fixed_phase_state(reconstructed,probe)==expected
    # Controller transitions are exactly 12 elapsed hours apart even when wall time jumps.
    state=fixed_phase_state(t['phase_schedule'],start)
    low=datetime.fromisoformat(state['phase_start_datetime']);high=datetime.fromisoformat(state['next_transition_datetime'])
    assert (high-low).total_seconds()==43200
    assert load_config_bundle(path)['recording']['timeline']['phase_schedule']['anchor_datetime']==t['phase_schedule']['anchor_datetime']


def test_manual_start_across_dst_preserves_controller_cycle_and_provenance(tmp_path):
    cfg=valid_config();t=cfg['recording']['timeline'];t.update(phases={'light_phase':'00:00','dark_phase':'12:00'})
    t=establish_schedule(t,'2026-03-28T12:00:00+01:00')
    t.update(datetime_mode='manual',manual_start_datetime='2026-03-30T12:00:00+02:00',manual_end_datetime='2026-04-01T12:00:00+02:00')
    cfg['recording']['timeline']=select_timing(t);path=tmp_path/'manual.json';save_config_bundle(path,cfg)
    main=json.loads(path.read_text())['recording']['timeline']
    assert main['phases']=={'light_phase':'01:00','dark_phase':'13:00'}
    assert json.loads(path.read_text())['acquisition']['datetime_provenance']=={'start_source':'manual_override','end_source':'manual_override'}
    reconstructed=establish_schedule(main,main['start_datetime'])['phase_schedule']
    assert fixed_phase_state(reconstructed,main['start_datetime'])==fixed_phase_state(t['phase_schedule'],main['start_datetime'])
    loaded=load_config_bundle(path)['recording']['timeline']
    assert loaded['phase_schedule']['anchor_datetime']==t['phase_schedule']['anchor_datetime']
    assert loaded['phases']==t['phases']


def test_no_executable_local_clock_schedule_remains():
    with pytest.raises(ValueError):validate_schedule({'mode':'legacy_local'})
    cfg=valid_config();cfg['recording']['timeline']['phase_schedule']={'mode':'legacy_local'}
    assert migrate_config(cfg)['recording']['timeline']['phase_schedule']['mode']=='fixed_cycle'


def source(index,first,last,start=None,end=None,status='unknown',basis='unknown',rotation=''):
    return dict(id=index,path=f'file{index}',board_id=1,port='COM5',recording_part=index,clock_group='1',rotation_group=rotation,
        first_pc_us=first*1_000_000,last_pc_us=last*1_000_000,recording_start=start,recording_end=end,stop_status=status,timing_basis=basis)


def stamp(second):return (datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(seconds=second)).isoformat()


def test_coverage_distinguishes_confirmed_gaps_from_silence_and_abrupt_ends():
    files=[source(1,10,20,stamp(5),stamp(25),'clean','acquisition_journal'),
           source(2,40,50,stamp(35),stamp(65),'error','acquisition_journal'), source(3,80,90)]
    c=recording_coverage(files,'UTC')[0]
    assert len(c['confirmed_interruptions'])==1
    assert c['confirmed_interruptions'][0]['duration_seconds']==10
    assert len(c['observed_record_gaps'])==2
    assert c['source_intervals'][1]['end_basis']=='abrupt_or_incomplete_stop'
    assert c['source_intervals'][1]['confirmed_stop'] is None
    assert c['source_intervals'][2]['end_basis']=='unknown'
    assert all(not i['last_record_is_failure_time'] for i in c['source_intervals'])


def test_overlaps_and_declared_rotation_do_not_invent_confirmed_interruptions():
    a=source(1,10,20,rotation='hourly');b=source(2,40,50,rotation='hourly');b['recording_part']=1
    c=recording_coverage([a,b],'UTC')[0]
    assert not c['observed_record_gaps'] and len(c['declared_rotation_boundaries'])==1
    c=recording_coverage([source(1,10,90),source(2,20,30),source(3,40,50)],'UTC')[0]
    assert not c['observed_record_gaps'] and not c['confirmed_interruptions']


def test_published_diagnostics_link_and_failed_job_keeps_previous_link(tmp_path):
    cfg=default_config('Link');cfg['recording']['layout']={};path=tmp_path/'Link.config.json';save_config_bundle(path,cfg)
    raw=tmp_path/'raw.txt';raw.write_text('A-1;0000A819;1;2026-09-01T12:00:00+02:00;1\n')
    settings=Settings([InputFile(str(raw))],str(tmp_path/'preprocessed_data'),str(path),whitelist=False,interpolate=False,parquet=False,csv=True)
    result=run(settings);main=json.loads(path.read_text());link=links(main)[LOG_LINK]
    assert resolve_link(path,link)==Path(result['diagnostic_path'])
    report=json.loads(resolve_link(path,link).read_text())
    assert report['status']=='completed' and report['experiment_timing']['status']=='updated'
    assert 'recordings' in report and report['outputs'] and report['processing']['settings_source']
    raw.write_text('A-2;0000A819;1;2026-09-01T12:00:00+02:00;1\n')
    with pytest.raises(ValueError):run(settings)
    assert links(json.loads(path.read_text()))[LOG_LINK]==link


def test_journal_coverage_includes_board_with_no_selected_reads(tmp_path):
    from deepecohab_recorder.preprocessing.recording_gaps import acquisition_journals, attach_journal_evidence
    root=tmp_path/'session_configs'/'run1';root.mkdir(parents=True)
    raw=tmp_path/'one.txt';raw.write_text('A-1;0000A819;1;2026-09-01T12:00:00+02:00;1\n')
    (root/'run.json').write_text(json.dumps({'devices':{
        'board1':{'config':{'board_id':1},'data_file':str(raw),'start_datetime':stamp(1),'end_datetime':stamp(20),'stop_status':'clean'},
        'board2':{'config':{'board_id':2},'data_file':'missing.csv','start_datetime':stamp(2),'end_datetime':None}}}))
    evidence,warnings=acquisition_journals(tmp_path,'UTC')
    assert len(evidence)==2 and not warnings
    inputs=[InputFile(str(raw))];attach_journal_evidence(inputs,evidence)
    assert inputs[0].timing_basis=='acquisition_journal' and inputs[0].stop_status=='clean'
    c=recording_coverage([source(1,10,15)],'UTC',evidence)
    empty=next(row for row in c if row['board_id']==2)
    assert empty['first_observed_record'] is None and empty['selected_files_with_records']==0
    assert empty['acquisition_journals'][0]['stop_status']=='unknown'


def test_ambiguous_journal_filenames_are_not_assumed_to_match(tmp_path):
    from deepecohab_recorder.preprocessing.recording_gaps import attach_journal_evidence
    spec=InputFile(str(tmp_path/'same.txt'))
    entries=[dict(board_id=1,data_file='C:/old/same.txt',recording_start=stamp(1),recording_end=stamp(2),stop_status='clean')]*2
    attach_journal_evidence([spec],entries)
    assert spec.start is None and spec.timing_basis=='unknown'
