"""Small runtime checkpoints in GUI state, with no per-session log files."""
from deepecohab_recorder.recording_history import device_state
from deepecohab_recorder.experiment_files import filename
from deepecohab_recorder.utils import read_json


def save_device(controller, key, value):
    sink = getattr(controller, "state_sink", None)
    if sink:
        sink(key, value)
    else:
        session = controller._session
        device_state(session.save_path, session.experiment_name, key, value)


def read_device(controller, key):
    session = controller._session
    path = session.save_path / filename(session.experiment_name, "acquisition_gui_state", "json")
    if not path.exists():
        return {}
    return read_json(path).get("state", {}).get("acquisition", {}).get("device_history", {}).get(key, {})


class DiscardRawLog:
    """Raw detections are already persisted; do not duplicate serial traffic."""
    def write(self, raw):
        pass
    def flush_due(self):
        pass
    def close(self):
        pass


class MalformedSamples:
    """Bound the number and size of examples; counters retain the total."""
    def __init__(self, samples):
        self.samples = samples
    def write(self, text):
        import json
        if len(self.samples) < 20:
            value = json.loads(text)
            raw = value.get("data_b64", "")
            value["sample_truncated"] = len(raw) > 4096
            value["data_b64"] = raw[:4096]
            self.samples.append(value)
    def flush(self):
        pass
    def close(self):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()
