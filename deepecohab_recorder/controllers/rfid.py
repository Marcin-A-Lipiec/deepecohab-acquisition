from __future__ import annotations
from collections import deque
import threading
import time
from deepecohab_recorder.controllers.serial_base import SerialCapture
from deepecohab_recorder.protocols.rfid import protocol_for_hardware
from deepecohab_recorder.utils import source_channel_id


class RFIDBoardController(SerialCapture):
    def __init__(self, config, max_queue=200_000):
        super().__init__(config, max_queue)
        self.kind = f"rfid_board{config.board_id}"
        self._data_lock = threading.RLock()
        self._reset_profile()

    def _reset_profile(self):
        self.protocol = protocol_for_hardware(self.config.hardware)
        self.start_command = self.protocol.start_command
        self._status = {
            channel: {"t": 0.0, "tag": ""}
            for channel in list(self.protocol.rfid_channels)
            + list(self.protocol.ttl_channels)
        }
        self._tag_counts = {}
        self._ttl_counts = {channel: 0 for channel in self.protocol.ttl_channels}
        self._events = deque(maxlen=8192)
        self._rfid_event_counter = 0

    def start(self, session):
        if self.is_running():
            raise RuntimeError("Device workers are still active.")
        self._reset_profile()
        super().start(session)

    def reconfigure(self, config):
        super().reconfigure(config)
        self._reset_profile()

    def _consume(self, raw, pc_time, mono_ns):
        record = self.protocol.parse(raw)
        with self._data_lock:
            self._status[record.logical_channel] = {
                "t": time.monotonic(),
                "tag": record.payload,
            }
            if record.is_ttl:
                self._ttl_counts[record.logical_channel] += 1
            else:
                if (
                    record.payload not in self._tag_counts
                    and len(self._tag_counts) >= 10000
                ):
                    raise ValueError(
                        "Unique tag limit exceeded; inspect raw diagnostics for corrupt tags."
                    )
                self._tag_counts[record.payload] = (
                    self._tag_counts.get(record.payload, 0) + 1
                )
                self._rfid_event_counter += 1
                self._events.append(
                    (self._rfid_event_counter, record.payload, record.logical_channel)
                )
        source = source_channel_id(
            record.logical_channel,
            self.config.board_id,
            self.config.com_port,
            record.is_ttl,
        )
        return (
            "event",
            f"{source};{record.payload};{record.board_time};{pc_time};{self.config.board_id}\n",
        )

    def get_status_snapshot(self):
        with self._data_lock:
            return {key: dict(value) for key, value in self._status.items()}

    def get_tag_counts_snapshot(self):
        with self._data_lock:
            return dict(self._tag_counts)

    def get_ttl_counts_snapshot(self):
        with self._data_lock:
            return dict(self._ttl_counts)

    def get_last_rfid_event(self):
        with self._data_lock:
            return self._events[-1] if self._events else (0, "", "")

    def events_since(self, cursor):
        with self._data_lock:
            return [event for event in self._events if event[0] > cursor]
