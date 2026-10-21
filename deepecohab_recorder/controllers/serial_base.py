"""Shared serial capture: bounded queues, writer readiness and complete draining."""

from __future__ import annotations
import base64
import json
from pathlib import Path
import queue
import threading
import time
import uuid
from deepecohab_recorder.clock import ExperimentClock
from deepecohab_recorder.experiment_files import available_prefix
from deepecohab_recorder.utils import sanitize_com_port
from .evidence import DiscardRawLog, MalformedSamples


class RawSerialLog:
    """Rotated byte-faithful JSONL chunks; concatenate decoded data_b64 to replay."""

    def __init__(self, path, clock, limit=64 * 1024 * 1024):
        self.path, self.clock, self.limit = Path(path), clock, limit
        self.part, self.size, self.handle = 0, 0, None
        self.last_flush = time.monotonic()
        self._open()

    def _open(self):
        path = (
            self.path
            if not self.part
            else self.path.with_name(
                f"{self.path.stem}_{self.part:04d}{self.path.suffix}"
            )
        )
        self.handle = path.open("x", encoding="utf-8", buffering=1024 * 1024)

    def write(self, raw):
        text = (
            json.dumps(
                {
                    "pc_datetime": self.clock.iso(),
                    "pc_monotonic_ns": time.monotonic_ns(),
                    "data_b64": base64.b64encode(raw).decode("ascii"),
                },
                separators=(",", ":"),
            )
            + "\n"
        )
        if self.size and self.size + len(text) > self.limit:
            self.handle.close()
            self.part += 1
            self.size = 0
            self._open()
        self.handle.write(text)
        self.size += len(text)
        self.flush_due()

    def flush_due(self):
        if time.monotonic() - self.last_flush >= 1:
            self.handle.flush()
            self.last_flush = time.monotonic()

    def close(self):
        if self.handle:
            self.handle.close()


class SerialCapture:
    kind = "serial"
    start_command = b""
    serial_timeout = 0.1

    def __init__(self, config, max_queue=100_000):
        self.config, self.max_queue = config, max_queue
        self._serial = None
        self._serial_lock = threading.RLock()
        self._stop = threading.Event()
        self._reader_done = threading.Event()
        self._writer_ready = threading.Event()
        self._reader_thread = self._writer_thread = None
        self._queue = queue.Queue(maxsize=max_queue)
        self.clock = ExperimentClock()
        self.last_error = ""
        self.started_iso = self.stopped_iso = ""
        self.lines_ok = self.lines_bad = self.lines_dropped = self.lines_written = (
            self.bytes_read
        ) = self.queue_peak = 0
        self.event_rows = self.status_rows = 0
        self.data_file = self.status_file = self.raw_file = self.unparsed_file = None
        self._running = False
        self.status_headers = ""

    def start(self, session):
        if self.is_running():
            raise RuntimeError("The previous device workers are still active.")
        if not self.config.com_port:
            raise ValueError("Select a COM port.")
        import serial

        self._session, self.clock = session, ExperimentClock(session.timezone)
        self._queue = queue.Queue(maxsize=self.max_queue)
        self._stop.clear()
        self._reader_done.clear()
        self._writer_ready.clear()
        self.last_error = self.started_iso = self.stopped_iso = ""
        self.lines_ok = self.lines_bad = self.lines_dropped = self.lines_written = (
            self.bytes_read
        ) = self.queue_peak = 0
        self.event_rows = self.status_rows = 0
        self.epoch_id = uuid.uuid4().hex[:12]
        root = session.save_path
        data_root = root / "raw_antenna_reads" if self.kind.startswith("rfid") else root
        data_root.mkdir(parents=True, exist_ok=True)
        prefix = available_prefix([root, data_root], f"{session.file_prefix}_{self.kind}_{sanitize_com_port(self.config.com_port)}")
        self.data_file = (
            data_root / f"{prefix}_antenna_data.csv"
            if self.kind.startswith("rfid")
            else data_root / f"{prefix}_data.csv"
        )
        self.status_file = (
            root
            / f"{prefix}_status.csv"
            if self.status_headers
            else None
        )
        self.raw_file = self.unparsed_file = None
        self.malformed_examples = []
        self._raw_log = self._bad_log = None
        try:
            # The raw detection writer must be ready before the board is initialized.
            self._raw_log = DiscardRawLog()
            self._bad_log = MalformedSamples(self.malformed_examples)
            self._writer_thread = threading.Thread(
                target=self._writer_loop, name=f"{self.kind}-writer", daemon=True
            )
            self._writer_thread.start()
            if not self._writer_ready.wait(3) or self.last_error:
                raise RuntimeError(
                    self.last_error or "Recording file writer did not become ready."
                )
            self._serial = self._open_port()
            try:
                self._serial.set_buffer_size(rx_size=1024 * 1024, tx_size=65536)
            except (AttributeError, OSError):
                pass
            if self.start_command:
                time.sleep(0.2)
                self._serial.write(self.start_command)
                self._serial.flush()
            self.started_iso = self.clock.iso()
            self._running = True
            self._reader_thread = threading.Thread(
                target=self._reader_loop, name=f"{self.kind}-reader", daemon=True
            )
            self._reader_thread.start()
        except Exception:
            self.request_stop()
            self._reader_done.set()
            self.wait(3)
            for handle in (self._raw_log, self._bad_log):
                if handle:
                    handle.close()
            raise

    def _open_port(self):
        import serial

        return serial.Serial(
            self.config.com_port,
            int(self.config.baudrate),
            timeout=self.serial_timeout,
            write_timeout=1,
        )

    def _poll_link(self):
        pass

    def _recover_link(self, error):
        return False

    def _reader_closed(self):
        pass

    def reconfigure(self, config):
        if self.is_running():
            raise RuntimeError("Cannot reconfigure a device with active workers.")
        self.config = config

    def is_running(self):
        return any(
            thread and thread.is_alive()
            for thread in (self._reader_thread, self._writer_thread)
        )

    def request_stop(self):
        if not self._stop.is_set() and self.started_iso:
            self.stopped_iso = self.clock.iso()
        self._stop.set()
        with self._serial_lock:
            if self._serial:
                try:
                    self._serial.close()
                except Exception as exc:
                    self.last_error = self.last_error or f"Serial close failed: {exc}"

    def wait(self, timeout=5):
        deadline = time.monotonic() + timeout
        for thread in (self._reader_thread, self._writer_thread):
            if thread and thread.is_alive():
                thread.join(max(0, deadline - time.monotonic()))
        self._running = self.is_running()
        if self._running:
            self.last_error = (
                self.last_error or "Stop timeout: workers still own this device."
            )

    def stop(self, timeout=5):
        self.request_stop()
        self.wait(timeout)

    def _put(self, kind, text):
        try:
            self._queue.put_nowait((kind, text))
            self.queue_peak = max(self.queue_peak, self._queue.qsize())
            return True
        except queue.Full:
            self.lines_dropped += 1
            return False

    def _reader_loop(self):
        buffer = b""
        try:
            with self._bad_log as bad:
                raw_log = self._raw_log
                try:
                    while not self._stop.is_set():
                        try:
                            self._poll_link()
                            chunk = self._serial.read(
                                min(max(1, self._serial.in_waiting), 65536)
                            )
                        except Exception as exc:
                            if self._recover_link(exc):
                                if buffer:
                                    bad.write(
                                        json.dumps(
                                            {
                                                "pc_datetime": self.clock.iso(),
                                                "error": "USB gap interrupted serial fragment",
                                                "data_b64": base64.b64encode(
                                                    buffer
                                                ).decode(),
                                            }
                                        )
                                        + "\n"
                                    )
                                    buffer = b""
                                continue
                            raise
                        if not chunk:
                            raw_log.flush_due()
                            continue
                        pc_time, mono_ns = self.clock.iso(), time.monotonic_ns()
                        self.bytes_read += len(chunk)
                        raw_log.write(chunk)
                        buffer += chunk
                        if len(buffer) > 1024 * 1024:
                            raise ValueError(
                                "Serial line exceeded 1 MiB without a terminator; recording stopped to protect the parser."
                            )
                        while b"\n" in buffer:
                            raw, buffer = buffer.split(b"\n", 1)
                            raw = raw.rstrip(b"\r")
                            if not raw:
                                continue
                            try:
                                kind, output = self._consume(raw, pc_time, mono_ns)
                                if output and self._put(kind, output):
                                    self.lines_ok += 1
                                    self.event_rows += kind == "event"
                                    self.status_rows += kind == "status"
                            except Exception as exc:
                                self.lines_bad += 1
                                bad.write(
                                    json.dumps(
                                        {
                                            "pc_datetime": pc_time,
                                            "error": str(exc),
                                            "data_b64": base64.b64encode(raw).decode(),
                                        }
                                    )
                                    + "\n"
                                )
                                bad.flush()
                finally:
                    raw_log.close()
                    if buffer.strip():
                        self.lines_bad += 1
                        bad.write(
                            json.dumps(
                                {
                                    "pc_datetime": self.clock.iso(),
                                    "error": "Unterminated serial fragment at recording stop",
                                    "data_b64": base64.b64encode(buffer).decode(),
                                }
                            )
                            + "\n"
                        )
                        bad.flush()
        except Exception as exc:
            if not self._stop.is_set():
                self.last_error = f"Serial acquisition failed: {exc}"
        finally:
            self.stopped_iso = self.stopped_iso or self.clock.iso()
            self.request_stop()
            try:
                self._reader_closed()
            except Exception as exc:
                self.last_error = (
                    self.last_error or f"Final diagnostic checkpoint failed: {exc}"
                )
            self._reader_done.set()

    def _writer_loop(self):
        handles, batch = {}, {"event": [], "status": []}
        next_flush = time.monotonic() + 0.2
        try:
            handles["event"] = self.data_file.open(
                "x", encoding="utf-8", newline="", buffering=1024 * 1024
            )
            if self.status_file:
                handles["status"] = self.status_file.open(
                    "x", encoding="utf-8", newline="", buffering=1024 * 1024
                )
                handles["status"].write(self.status_headers)
                handles["status"].flush()
            self._writer_ready.set()
            while not (self._reader_done.is_set() and self._queue.empty()):
                try:
                    kind, value = self._queue.get(timeout=0.1)
                    batch[kind].append(value)
                except queue.Empty:
                    pass
                if (
                    time.monotonic() >= next_flush
                    or sum(map(len, batch.values())) >= 2000
                ):
                    for kind, rows in batch.items():
                        if rows:
                            handles[kind].writelines(rows)
                            handles[kind].flush()
                            self.lines_written += len(rows)
                            rows.clear()
                    next_flush = time.monotonic() + 0.2
            for kind, rows in batch.items():
                if rows:
                    handles[kind].writelines(rows)
                    handles[kind].flush()
                    self.lines_written += len(rows)
        except Exception as exc:
            self.last_error = f"Recording writer failed: {exc}"
            self.request_stop()
            self._writer_ready.set()
        finally:
            for handle in handles.values():
                handle.close()
            self._running = False

    def status_text(self):
        state = (
            "STOPPING"
            if self._stop.is_set() and self.is_running()
            else "RECORDING" if self.is_running() else "STOPPED"
        )
        return (
            f"{state} | received={self.lines_ok:,} written={self.lines_written:,} bad={self.lines_bad:,} dropped={self.lines_dropped:,} queue={self._queue.qsize():,}"
            + (f" | {self.last_error}" if self.last_error else "")
        )
