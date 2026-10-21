from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


def safe_slug(text: str, fallback: str = "experiment") -> str:
    text = (text or "").strip()
    if not text:
        return fallback
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^A-Za-z0-9._-]", "", text)
    return text or fallback


def sanitize_com_port(port: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]", "", (port or "").upper())
    return value or "COM_UNKNOWN"


def source_channel_id(
    channel: str, board_id: int, com_port: str, is_ttl: bool = False
) -> str:
    """Physical input plus logical board; COM is connection metadata only."""
    ch = str(channel).strip().upper()
    if is_ttl and not ch.startswith("TTL"):
        ch = f"TTL{ch}"
    from .source_identity import source_id
    return source_id(ch, board_id)


def list_serial_ports() -> List[str]:
    try:
        import serial.tools.list_ports

        return [p.device for p in serial.tools.list_ports.comports()]
    except Exception:
        return []


def atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as f:
        value = json.load(f)
    if not isinstance(value, dict):
        raise ValueError("Configuration JSON must contain an object at the top level.")
    return value


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def flatten_mapping(value: Any, prefix: str = "") -> Iterable[Tuple[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from flatten_mapping(child, child_prefix)
    elif isinstance(value, list):
        yield prefix, _csv_value(value)
    else:
        yield prefix, _csv_value(value)


def write_flat_config_csv(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(["key", "value"])
            for key, value in flatten_mapping(data):
                writer.writerow([key, value])
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def utc_iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def local_iso_now() -> str:
    return dt.datetime.now().astimezone().isoformat()


def apply_dark_theme(app: Any) -> None:
    """Compatibility entry point for the shared dark theme, without saving a preference."""
    from .ui.theme import manager
    manager().set_mode("dark", persist=False)
