"""Live DAQ identifiers; archived source numbers are deliberately unrestricted."""

from deepecohab_recorder.models import NEW_DAQ

RFID_BOARD_IDS = tuple(range(1, 5))
RFID_DEVICE_KEYS = tuple(f"board{i}" for i in RFID_BOARD_IDS)
DEVICE_KEYS = (*RFID_DEVICE_KEYS, "camera", "trigger")


def board_id(key):
    if key not in RFID_DEVICE_KEYS:
        raise ValueError(f"Unsupported live RFID device: {key}")
    return int(key.removeprefix("board"))


def board_config_key(key):
    return f"board_{board_id(key)}"


def visible_board_count(acquisition):
    """Honor saved presentation settings while never concealing an active board."""
    boards = acquisition.get("boards", {})
    active = [int(b.get("board_id", 0)) for b in boards.values()
              if b.get("board_id") in RFID_BOARD_IDS and b.get("active")]
    saved = acquisition.get("ui", {}).get("rfid_board_count")
    if isinstance(saved, int) and not isinstance(saved, bool) and saved in RFID_BOARD_IDS:
        return max(saved, max(active, default=1))
    meaningful = [int(b.get("board_id", 0)) for b in boards.values()
                  if b.get("board_id") in RFID_BOARD_IDS and
                  (b.get("active") or str(b.get("com_port", "")).strip() or
                   b.get("hardware", NEW_DAQ) != NEW_DAQ or
                   acquisition.get("launch", {}).get(f"board{b['board_id']}"))]
    return max(meaningful, default=1)
