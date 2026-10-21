"""Cancellable tag/source discovery, preserving exclusive port ownership."""

import copy
import queue
import threading
from deepecohab_recorder.protocols.rfid import protocol_for_hardware
from deepecohab_recorder.utils import source_channel_id


class MatchSession:
    def __init__(self, services, boards):
        self.services, self.boards = services, copy.deepcopy(boards)
        self.results = queue.Queue(maxsize=500)
        self.cancel = threading.Event()
        self.error = ""
        self.thread = threading.Thread(
            target=self._worker, daemon=True, name="tag-matching"
        )

    def start(self):
        self.thread.start()

    def is_running(self):
        return self.thread.is_alive()

    def _result(self, board, tag, channel):
        try:
            self.results.put_nowait(
                (
                    tag,
                    source_channel_id(channel, board["board_id"], board["com_port"]),
                    board["board_id"],
                )
            )
        except queue.Full:
            pass

    def _worker(self):
        connections, readers = [], []
        try:
            for board in self.boards.values():
                if not board.get("active") or not board.get("com_port"):
                    continue
                key = f"board{board['board_id']}"
                controller = self.services.controllers.get(key)
                if self.services.states[key] == "Running" and controller:
                    readers.append(
                        [board, controller, controller.get_last_rfid_event()[0]]
                    )
                    continue
                if self.services.busy(key):
                    raise RuntimeError(
                        f"{key} is starting/stopping. Wait before matching."
                    )
                import serial

                port = board["com_port"]
                self.services.ports.acquire(port, "tag matching")
                try:
                    ser = serial.Serial(
                        port,
                        board.get("baudrate", 115200),
                        timeout=0.05,
                        write_timeout=1,
                    )
                except Exception:
                    self.services.ports.release(port, "tag matching")
                    raise
                protocol = protocol_for_hardware(board["hardware"])
                connections.append([board, ser, protocol, b""])
                if self.cancel.wait(0.2):
                    return
                if protocol.start_command:
                    ser.write(protocol.start_command)
                    ser.flush()
            if not connections and not readers:
                raise ValueError("Enable an RFID board and choose its COM port first.")
            while not self.cancel.is_set():
                for item in readers:
                    board, controller, cursor = item
                    for number, tag, channel in controller.events_since(cursor):
                        item[2] = number
                        self._result(board, tag, channel)
                for item in connections:
                    board, ser, protocol, buffer = item
                    buffer += ser.read(min(max(1, ser.in_waiting), 65536))
                    if len(buffer) > 1024**2:
                        raise ValueError(
                            "Malformed matching stream: no line terminator."
                        )
                    while b"\n" in buffer:
                        raw, buffer = buffer.split(b"\n", 1)
                        try:
                            record = protocol.parse(raw.rstrip(b"\r"))
                            if not record.is_ttl:
                                self._result(
                                    board, record.payload, record.logical_channel
                                )
                        except ValueError:
                            pass
                    item[3] = buffer
                self.cancel.wait(0.03)
        except Exception as exc:
            self.error = str(exc)
        finally:
            for board, ser, _, _ in connections:
                try:
                    ser.close()
                finally:
                    self.services.ports.release(board["com_port"], "tag matching")
