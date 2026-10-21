"""Per-user video-tool preferences and one executable resolver for all consumers."""
from dataclasses import asdict, dataclass
from pathlib import Path
import os
import shutil
import subprocess

from . import preferences
from .preferences import preferences_path


class VideoToolError(ValueError):
    pass


def read_preferences():
    path = preferences_path()
    try:
        data = preferences.read_preferences()
        paths = data.get('video_tools', {})
        if not isinstance(paths, dict) or any(not isinstance(paths.get(name, ''), str) for name in ('ffmpeg', 'ffprobe')):
            raise ValueError('Video-tool paths must be strings.')
        return data
    except (ValueError, OSError) as exc:
        raise VideoToolError(f'Cannot read {path}: {exc}. Repair this application preferences file; experiment metadata is separate.') from exc


def normalized_path(value):
    value = str(value).strip()
    if not value:
        return ''
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise VideoToolError('Choose an absolute executable path, so shortcuts work from any folder.')
    if not path.is_file():
        raise VideoToolError(f'Executable not found: {path}. Choose the program file, not its folder.')
    if os.name == 'nt' and path.suffix.lower() != '.exe':
        raise VideoToolError(f'Choose an .exe file: {path}')
    if not os.access(path, os.X_OK):
        raise VideoToolError(f'This file cannot be executed: {path}')
    return str(path.resolve())


def save_video_paths(ffmpeg='', ffprobe=''):
    # Read before replacing: preserve future preferences and refuse to silently
    # overwrite a corrupt file. These paths never enter scientific config files.
    read_preferences()
    paths = dict(ffmpeg=normalized_path(ffmpeg), ffprobe=normalized_path(ffprobe))
    try:
        preferences.update_section('video_tools', paths)
    except preferences.PreferencesError as exc:
        raise VideoToolError(str(exc)) from exc


@dataclass(frozen=True)
class Executable:
    name: str
    path: str | None
    source: str
    error: str = ''

    def require(self):
        if not self.path:
            raise VideoToolError(self.error)
        return self.path

    def to_dict(self):
        return asdict(self)


def resolve_executable(name, configured='', sibling=None):
    if name not in ('ffmpeg', 'ffprobe'):
        raise ValueError(f'Unsupported video tool: {name}')
    if configured:
        try:
            return Executable(name, normalized_path(configured), 'configured')
        except VideoToolError as exc:
            return Executable(name, None, 'configured', f'{name}: {exc}')
    if sibling:
        candidate = Path(sibling).with_name(name + ('.exe' if os.name == 'nt' else ''))
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return Executable(name, str(candidate.resolve()), 'beside FFmpeg')
    path = shutil.which(name)
    if path:
        return Executable(name, str(Path(path).resolve()), 'PATH')
    return Executable(name, None, 'automatic',
        f'{name} was not found. Open System check to choose its executable, or add its bin folder to PATH.')


def resolve_video_tools(*, ffmpeg=None, ffprobe=None, preferences=None):
    paths = (read_preferences() if preferences is None else preferences).get('video_tools', {})
    encoder = resolve_executable('ffmpeg', paths.get('ffmpeg', '') if ffmpeg is None else ffmpeg)
    probe = resolve_executable('ffprobe', paths.get('ffprobe', '') if ffprobe is None else ffprobe,
                               sibling=encoder.path)
    return {'ffmpeg': encoder, 'ffprobe': probe}


def subprocess_options():
    """External video/check processes should not flash console windows."""
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
