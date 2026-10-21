"""Decode and count recorded frames in a camera inventory (read-only)."""

import argparse
import json
from pathlib import Path
import subprocess
from deepecohab_recorder.video_tools import resolve_video_tools, subprocess_options


def verify(inventory_path, session_key=None, *, ffmpeg=None, ffprobe=None):
    path = Path(inventory_path)
    inventory = json.loads(path.read_text(encoding="utf-8"))
    if inventory.get('format') != 'acquisition_gui_state' and 'parts' not in inventory:
        link = inventory.get('acquisition', {}).get('_provenance', {}).get('acquisition_gui_state_path')
        if not link:
            raise ValueError('Select the main config, GUI state, or a legacy camera inventory.')
        path = path.parent / link
        inventory = json.loads(path.read_text(encoding='utf-8'))
    if inventory.get('format') == 'acquisition_gui_state':
        history = inventory.get('state', {}).get('acquisition', {}).get('device_history', {})
        selected = [v for k, v in history.items() if k.endswith(':camera_inventory') and (session_key is None or k == session_key)]
        inventory = {'parts': [part for value in selected for part in value.get('parts', [])]}
    results, ok = [], bool(inventory.get('parts'))
    tools = resolve_video_tools(ffmpeg=ffmpeg, ffprobe=ffprobe) if inventory.get('parts') else {}
    for part in inventory["parts"]:
        total = 0
        for item in part["files"]:
            movie = path.parent / item["file"]
            probe = subprocess.run(
                [
                    tools['ffprobe'].require(),
                    "-v",
                    "error",
                    "-count_frames",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=nb_read_frames",
                    "-of",
                    "json",
                    str(movie),
                ],
                capture_output=True,
                text=True,
                **subprocess_options(),
            )
            decoded = subprocess.run(
                [tools['ffmpeg'].require(), "-nostdin", "-v", "error", "-i", str(movie), "-f", "null", "-"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                **subprocess_options(),
            )
            try:
                count = int(json.loads(probe.stdout)["streams"][0]["nb_read_frames"])
            except (ValueError, KeyError, IndexError):
                count = None
            valid = (
                probe.returncode == 0
                and decoded.returncode == 0
                and count is not None
                and not decoded.stderr.strip()
            )
            total += count or 0
            ok &= valid
            results.append(
                {
                    "file": str(movie),
                    "decoded_frames": count,
                    "decode_ok": valid,
                    "error": decoded.stderr.decode(errors="replace")[-2000:],
                }
            )
        matched = (
            total == part["submitted_frames"]
            and not part["forced"]
            and part["return_code"] == 0
        )
        ok &= matched
        results.append(
            {
                "part": part["part"],
                "submitted_frames": part["submitted_frames"],
                "decoded_frames": total,
                "count_matches_clean_part": matched,
            }
        )
    return {"ok": bool(ok), "results": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory")
    parser.add_argument("--session-key", help="Verify only this camera inventory key in GUI state.")
    parser.add_argument('--ffmpeg', help='FFmpeg executable path; defaults to application preferences/PATH.')
    parser.add_argument('--ffprobe', help='ffprobe executable path; defaults to preferences/FFmpeg folder/PATH.')
    args = parser.parse_args(argv)
    try:
        report = verify(args.inventory, args.session_key, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)
    except (ValueError, OSError) as exc:
        report = {'ok': False, 'error': str(exc)}
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
