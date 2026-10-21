"""Exclusive serial port ownership, shared by capture and tag matching."""

import threading


class PortRegistry:
    def __init__(self):
        self._owners, self._lock = {}, threading.RLock()

    def acquire(self, port, owner):
        if not port:
            raise ValueError("Select a COM port.")
        with self._lock:
            key = port.upper()
            if key in self._owners:
                raise RuntimeError(f"{port} is already in use by {self._owners[key]}.")
            self._owners[key] = owner

    def release(self, port, owner):
        with self._lock:
            if self._owners.get(port.upper()) == owner:
                del self._owners[port.upper()]

    def snapshot(self):
        with self._lock:
            return dict(self._owners)
