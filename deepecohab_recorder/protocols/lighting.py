"""GIGA v5 control protocol."""

from deepecohab_recorder.protocols.control import (
    ControlInbox,
    DeviceStatus,
    parse_status,
)


def verify_lighting(status, expected_uid=None):
    if status.kind != "lighting":
        raise ValueError("The selected COM port is not a lighting controller.")
    if expected_uid and status.uid != expected_uid:
        raise ValueError(
            "Lighting device identity changed; select the intended controller."
        )
    return status
