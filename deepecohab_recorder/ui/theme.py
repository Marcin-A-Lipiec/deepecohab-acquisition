"""DeepEcoHab design tokens, desktop fonts, icons and live theme preferences."""
from functools import lru_cache
import json
import logging
import re
import sys

from PySide6.QtCore import QByteArray, QObject, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPalette, QPixmap, QTextCharFormat
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QCalendarWidget

from deepecohab_recorder.preferences import PreferencesError, read_preferences, update_section
from deepecohab_recorder.runtime_paths import asset

TOKENS = json.loads(asset('theme.json').read_text(encoding='utf-8'))
MODES = ('system', 'light', 'dark')
_logger = logging.getLogger(__name__)


def system_theme():
    app = QApplication.instance()
    if app is not None:
        scheme = app.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return 'dark'
        if scheme == Qt.ColorScheme.Light:
            return 'light'
    if sys.platform == 'win32':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
                return 'light' if winreg.QueryValueEx(key, 'AppsUseLightTheme')[0] else 'dark'
        except OSError:
            pass
    return 'light'


def load_fonts(app):
    if getattr(app, '_deepecohab_fonts', None) is not None:
        return app._deepecohab_fonts
    loaded = []
    for name in ('Geist-Regular.ttf', 'Geist-SemiBold.ttf', 'GeistMono-Regular.ttf'):
        try:
            identifier = QFontDatabase.addApplicationFontFromData(QByteArray(asset('fonts/' + name).read_bytes()))
            if identifier < 0:
                raise ValueError('Qt could not load the font')
            loaded += QFontDatabase.applicationFontFamilies(identifier)
        except (OSError, ValueError) as exc:
            _logger.warning('Font %s is unavailable; using the desktop fallback: %s', name, exc)
    families = QFontDatabase.families()
    sans = 'Geist' if 'Geist' in loaded else ('Segoe UI' if 'Segoe UI' in families else app.font().family())
    mono = 'Geist Mono' if 'Geist Mono' in loaded else QFontDatabase.systemFont(QFontDatabase.FixedFont).family()
    app._deepecohab_fonts = (sans, mono)
    return app._deepecohab_fonts


class ThemeManager(QObject):
    changed = Signal(str)
    mode_changed = Signal(str)

    def __init__(self, app):
        super().__init__(app)
        self.app, self.mode, self.effective, self.preference_error = app, 'system', '', ''
        self.sans, self.mono = load_fonts(app)
        try:
            appearance = read_preferences().get('appearance', {})
            if not isinstance(appearance, dict) or appearance.get('theme', 'system') not in MODES:
                raise PreferencesError('Unknown appearance preference; System theme is being used.')
            self.mode = appearance.get('theme', 'system')
        except PreferencesError as exc:
            self.preference_error = str(exc)
            _logger.warning('Appearance preference unavailable: %s', exc)
        app.setStyle('Fusion')
        app.setFont(QFont(self.sans, 10))
        app.styleHints().colorSchemeChanged.connect(self.refresh_system)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh_system)
        self.timer.start()
        self.apply()

    def set_mode(self, mode, *, persist=True):
        if mode not in MODES:
            raise ValueError(f'Unknown theme: {mode}')
        if persist:
            update_section('appearance', {'theme': mode})
            self.preference_error = ''
        self.mode = mode
        self.apply()
        self.mode_changed.emit(mode)

    def refresh_system(self, *args):
        if self.mode == 'system':
            self.apply()

    def apply(self):
        effective = system_theme() if self.mode == 'system' else self.mode
        if effective == self.effective:
            return
        self.effective = effective
        self.colors = TOKENS[effective]
        c = self.colors
        palette = QPalette()
        roles = {
            'Window': 'ground', 'WindowText': 'ink', 'Base': 'surface', 'AlternateBase': 'sunken',
            'Text': 'ink', 'Button': 'surface', 'ButtonText': 'ink', 'ToolTipBase': 'surface',
            'ToolTipText': 'ink', 'Highlight': 'accent', 'HighlightedText': 'accent-ink',
            'Link': 'accent-text', 'LinkVisited': 'device', 'PlaceholderText': 'ink-3',
            'Light': 'surface', 'Midlight': 'sunken', 'Mid': 'line-strong', 'Dark': 'line-strong', 'Shadow': 'line',
        }
        for name, role in roles.items():
            palette.setColor(getattr(QPalette, name), QColor(c[role]))
        for name in ('WindowText', 'Text', 'ButtonText', 'PlaceholderText', 'HighlightedText'):
            palette.setColor(QPalette.Disabled, getattr(QPalette, name), QColor(c['ink-3']))
        palette.setColor(QPalette.Disabled, QPalette.Base, QColor(c['sunken']))
        palette.setColor(QPalette.Disabled, QPalette.Highlight, QColor(c['line']))
        self.app.setPalette(palette)
        substitutions = dict(c, sans=self.sans, mono=self.mono)
        for name in ('checkbox', 'radio', 'mixed', 'checkbox-disabled', 'radio-disabled', 'mixed-disabled',
                     'arrow-down', 'arrow-up', 'arrow-left', 'arrow-right', 'arrow-up-disabled', 'arrow-down-disabled'):
            substitutions[name] = str(asset(f'theme/{effective}-{name}.svg')).replace('\\', '/')
        template = asset('theme.qss').read_text(encoding='utf-8')
        stylesheet = re.sub(r'@([a-z][a-z0-9-]*)', lambda match: substitutions[match[1]], template)
        self.app.setStyleSheet(stylesheet)
        self.app.setProperty('deepecohabTheme', effective)
        self.changed.emit(effective)


def manager():
    app = QApplication.instance()
    if app is None:
        raise RuntimeError('Create QApplication before requesting its theme manager.')
    current = getattr(app, '_deepecohab_theme', None)
    if current is None:
        current = app._deepecohab_theme = ThemeManager(app)
    return current


def colors():
    app = QApplication.instance()
    current = getattr(app, '_deepecohab_theme', None) if app is not None else None
    return current.colors if current is not None else TOKENS[system_theme()]


def color(role):
    return QColor(colors()[role])


def font(points=10, bold=False, mono=False):
    app = QApplication.instance()
    sans, fixed = load_fonts(app) if app is not None else ('Segoe UI', 'Consolas')
    return QFont(fixed if mono else sans, points, QFont.DemiBold if bold else QFont.Normal)


@lru_cache(maxsize=160)
def _icon(name, effective, role):
    raw = asset('icons/' + name + '.svg').read_text(encoding='utf-8')
    result = QIcon()
    for mode, token in ((QIcon.Normal, role), (QIcon.Active, 'accent-text'),
                        (QIcon.Selected, 'accent-ink'), (QIcon.Disabled, 'ink-3')):
        svg = raw.replace('stroke="#000"', f'stroke="{TOKENS[effective][token]}"')
        renderer = QSvgRenderer(QByteArray(svg.encode('utf-8')))
        for size in (16, 20, 24, 32):
            pixmap = QPixmap(size * 2, size * 2)
            pixmap.setDevicePixelRatio(2)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            renderer.render(painter, QRectF(0, 0, size, size))
            painter.end()
            result.addPixmap(pixmap, mode)
    return result


def icon(name, role='ink-2'):
    return _icon(name, manager().effective, role)


def indicator_style(active, size=14):
    c = colors()
    return (f'border-radius:{size // 2}px; min-width:{size}px; max-width:{size}px; '
            f'min-height:{size}px; max-height:{size}px; background:{c["good"] if active else c["line-strong"]};')


class ThemedCalendar(QCalendarWidget):
    """Keep Qt's date behavior while matching weekend colors to the current theme."""
    def __init__(self, parent=None):
        super().__init__(parent)
        manager().changed.connect(self.apply_theme)
        self.apply_theme()

    def apply_theme(self, *args):
        for day in (Qt.Saturday, Qt.Sunday):
            display = QTextCharFormat(self.weekdayTextFormat(day))
            display.setForeground(color('bad'))
            self.setWeekdayTextFormat(day, display)
