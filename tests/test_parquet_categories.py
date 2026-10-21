"""Verify the five-column export with the downstream Polars reader."""
import csv
from tests.helpers import result_fingerprint
from datetime import datetime, timedelta
import hashlib

import pytest
import pyarrow as pa
import pyarrow.parquet as pq

from tests import test_preprocessing as previous

pl = pytest.importorskip('polars', reason='Install Polars to verify downstream schema compatibility')
TAGS = ['0265E61A', '12F5FF19', '3AE5FF19', '3AFE9F1A', '6D68A819', 'C046E61A',
        'C8E2FF19', 'D5969C1A', 'DD43E61A', 'EE1CEC1A', 'F02AE61A']


@pytest.fixture
def experiment():
    fixture = previous.PreprocessingTests()
    fixture.setUp()
    try:
        yield fixture
    finally:
        fixture.tearDown()


def expected_schema(tags):
    return pl.Schema({
        'datetime': pl.Datetime('us', 'Europe/Warsaw'),
        'antenna': pl.Categorical(),
        'time_under': pl.Duration('us'),
        'animal_id': pl.Enum(tags),
        'internal_board_timestamp': pl.Datetime('us', 'Europe/Warsaw'),
        'inserted': pl.Boolean,
    })


def test_categorical_across_row_groups_keeps_five_columns_and_all_values(experiment):
    e = experiment
    e.cfg['recording']['cohort'] = {'n_mice': len(TAGS), 'animals': [
        {'tag': tag, 'subject_name': str(i), 'date_of_birth': '2024-01-01', 'mouse_line':'Test', 'genotype':'WT','genetic_background':'C57BL/6J','sex':'Male'} for i, tag in enumerate(TAGS)]}
    # Different antenna dictionaries in consecutive Parquet row groups; one
    # configured animal is intentionally absent from the observations.
    events = [(i * 500, 'A' if i < 5000 else 'B', TAGS[i % 10]) for i in range(5010)]
    events += [(2506000, 'C', TAGS[0]), (2507000, '10', '00000001')]
    source = e.raw('categorical.txt', events)
    e.process([source], parquet=True, routes={'C-1': 'auxiliary'})
    outputs = [o for o in e.result['outputs'] if o['file'].endswith('.parquet')]
    assert {o['group'] for o in outputs} == {'rfid', 'device_antenna1', 'TTL_data1'}
    for output in outputs:
        path = e.output / output['file']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == result_fingerprint(e.result['diagnostic_path'], path)
        assert pl.read_parquet_schema(path) == expected_schema(TAGS)
        assert pl.scan_parquet(path).collect_schema() == expected_schema(TAGS)
        frame = pl.read_parquet(path)
        assert frame.schema == expected_schema(TAGS)
        assert pl.read_parquet(path, use_pyarrow=True).schema == expected_schema(TAGS)
        assert not (pq.read_schema(path).field('antenna').metadata or {}).get(b'_PL_ENUM_VALUES2')
        with path.with_suffix('.csv').open(newline='') as handle:
            rows = list(csv.DictReader(handle))
        assert frame.height == len(rows)
        assert frame['antenna'].cast(pl.String).to_list() == [r['antenna'] for r in rows]
        assert frame['animal_id'].cast(pl.String).to_list() == [r['animal_id'] or None for r in rows]
        assert frame['datetime'].to_list() == [datetime.fromisoformat(r['datetime']) for r in rows]
        assert frame['time_under'].to_list() == [timedelta(milliseconds=int(r['time_spent'])) for r in rows]
        assert frame['internal_board_timestamp'].to_list() == [datetime.fromisoformat(r['internal_board_timestamp']) for r in rows]
        if output['group'] == 'rfid':
            assert pq.ParquetFile(path).num_row_groups == 2
            assert frame['antenna'].cast(pl.String).to_list() == ['A-1'] * 5000 + ['B-1'] * 10
    schema = e.summary['processing']['formats']['parquet']
    assert schema['version'] == 5
    assert schema['columns'] == list(expected_schema(TAGS))
    assert schema['antenna'] == 'dictionary categorical / Polars Categorical'


def test_ttl_without_animals_keeps_null_enum_and_board_timestamps(experiment):
    e = experiment
    e.cfg['recording']['cohort'] = {'n_mice': 0, 'animals': []}
    e.process([e.raw('ttl.txt', [(0, '10', '00000001')])], parquet=True, whitelist=False)
    path = next(e.output.glob('*.parquet'))
    assert pl.read_parquet_schema(path) == expected_schema([])
    frame = pl.read_parquet(path)
    assert frame['antenna'].cast(pl.String).to_list() == ['TTL1-1']
    assert frame['animal_id'].null_count() == 1
    assert frame['internal_board_timestamp'].null_count() == 0
    assert pq.read_schema(path).field('antenna').type == pa.dictionary(pa.uint32(), pa.string())
