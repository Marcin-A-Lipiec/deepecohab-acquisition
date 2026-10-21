import time
import types
from deepecohab_recorder.models import SessionSnapshot, DAQ_V15
from deepecohab_recorder.schema import default_config, normalize_animals
from deepecohab_recorder.topology import preset_layout, normalize_layout


class FakeSerial:
    def __init__(self, payload=b"", fail_read=False):
        self.buffer = bytearray(payload)
        self.is_open = True
        self.writes = []
        self.fail_read = fail_read

    @property
    def in_waiting(self):
        return len(self.buffer)

    def read(self, count=1):
        if self.fail_read:
            raise OSError("synthetic USB disconnect")
        if not self.is_open or not self.buffer:
            time.sleep(0.002)
            return b""
        result = bytes(self.buffer[:count])
        del self.buffer[:count]
        return result

    def readline(self):
        i = self.buffer.find(b"\n")
        return self.read(i + 1 if i >= 0 else len(self.buffer) or 1)

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def flush(self):
        pass

    def set_buffer_size(self, **kwargs):
        pass

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class SerialFactory(types.ModuleType):
    def __init__(self, payloads):
        super().__init__("serial")
        self.payloads = list(payloads)
        self.instances = []

    def Serial(self, *args, **kwargs):
        item = self.payloads.pop(0)
        if isinstance(item, Exception):
            raise item
        result = item if isinstance(item, FakeSerial) else FakeSerial(item)
        self.instances.append(result)
        return result


def wait_for(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for asynchronous operation")


def snapshot(root):
    return SessionSnapshot(
        "Test",
        str(root),
        "test",
        "2026-09-01T13:20:00+02:00",
        "Test_fixed_prefix",
        "Europe/Warsaw",
    )


def valid_config():
    cfg = default_config("Test")
    cfg["acquisition"]["boards"]["board_1"].update(com_port="COM5", hardware=DAQ_V15)
    cfg["acquisition"]["boards"]["board_2"].update(com_port="COM6", hardware=DAQ_V15)
    cfg["recording"]["cohort"] = normalize_animals(
        {
            "n_mice": 1,
            "animals": [
                {
                    "tag": "1A63A81904",
                    "subject_name": "ID1",
                    "mouse_line": "Test", "genotype": "WT", "genetic_background": "C57BL/6J", "sex": "Male",
                    "date_of_birth": "2024-11-28",
                }
            ],
        }
    )
    layout = preset_layout()
    for index, tunnel in enumerate(layout["tunnels"]):
        tunnel["antennas"] = [
            f"{chr(65 + index * 2)}COM5",
            f"{chr(66 + index * 2)}COM5",
        ]
    cfg["recording"]["layout"] = normalize_layout(layout, cfg["acquisition"]["boards"])
    return cfg


def inserted_rows(report, directory):
    """Resolve v11 row/method references for unchanged scientific assertions."""
    import csv
    from datetime import datetime, timedelta
    from pathlib import Path
    from zoneinfo import ZoneInfo
    streams, outputs = {}, {}
    for output in report['outputs']:
        outputs.setdefault(output['stream'], []).append(output['file'])
        if output['file'].endswith('.csv'):
            with (Path(directory)/output['file']).open() as handle:
                streams[output['stream']] = list(csv.DictReader(handle))
    result = []
    for insert in report['interpolations']:
        row = streams[insert['stream']][insert['row']]
        method = dict(report['inference_methods'][insert['method']])
        for key, section, scope in (('mouse_median','transition_medians','mouse'),('cohort_median','cohort_medians','cohort')):
            if key in method:
                median = report[section][method[key]]
                method.update(median_samples=median['samples'],median_scope=scope,transit_ms=median['median_ms'])
        def observed(ref):
            return streams[ref['stream']][ref['row']] if isinstance(ref,dict) else streams[insert['stream']][ref]
        prev, follow = observed(insert['previous']), observed(insert['following'])
        method['observed_anchors'] = {'previous':{'antenna':prev['antenna'],'actual_end_datetime':insert['previous_actual_end']}, 'following':{'antenna':follow['antenna'],'start_datetime':follow['datetime']}}
        method['topology_status'] = 'single_missing' if method['inference_type']=='standard' else 'unique_route'
        result.append(dict(row, type='output_record', is_interpolated=True,
                           output_antenna=row['antenna'], row_number=insert['row'], duration_ms=int(row['time_spent']),
                           method=method,output_files=outputs[insert['stream']],
                           estimated_end_datetime=(datetime.fromisoformat(row['datetime'])+timedelta(milliseconds=int(row['time_spent']))).isoformat(timespec='microseconds')))
    return result


def preprocessing_context(report, diagnostic_path):
    """Read executed settings through the public v12 link, not live GUI options."""
    import json
    from pathlib import Path
    source = report['processing'].get('settings_source')
    if not source:
        return report['processing']
    document = json.loads((Path(diagnostic_path).parent/source['file']).read_text())
    for key in source['section'].split('.'):
        document = document[key]
    return document


def result_fingerprint(diagnostic_path, output_path):
    from pathlib import Path
    import json
    from deepecohab_recorder.config_bundle import load_config_bundle, relative_link
    from deepecohab_recorder.preprocessing.result_state import result_integrity
    diagnostic_path = Path(diagnostic_path)
    log = json.loads(diagnostic_path.read_text())
    saved = result_integrity(load_config_bundle(diagnostic_path.parent/log['config_file']))
    return saved['output_files'][relative_link(output_path, diagnostic_path)]
