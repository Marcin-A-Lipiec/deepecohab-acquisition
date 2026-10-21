"""Render all existing pages and auxiliary controls without opening hardware.

Run with --scale 1, 1.25, 1.5 or 2 to check desktop scaling in a fresh Qt process.
The resulting HTML and PNGs are local review artifacts, not application assets.
"""
import argparse
import html
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scale', type=float, default=1)
    args = parser.parse_args()
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    os.environ['QT_SCALE_FACTOR'] = str(args.scale)
    os.environ['DEEPECOHAB_ACQUISITION_HOME'] = str(directory / 'application data')
    from PySide6.QtCore import Qt, qInstallMessageHandler
    from PySide6.QtGui import QImage, QPainter, QFontInfo
    from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QCheckBox, QRadioButton
    from deepecohab_recorder import __version__
    from deepecohab_recorder.main_window import MainWindow
    from deepecohab_recorder.ui.registry import PAGES
    from deepecohab_recorder.ui.theme import manager, font, ThemedCalendar

    warnings = []
    qInstallMessageHandler(lambda kind, context, message: warnings.append(message))
    app = QApplication([])
    theme = manager()
    window = MainWindow()
    window.widgets['setup'].directory_edit.setText(str(directory / 'experiment'))
    window._sync_context()
    window.services.shutdown()  # Static rendering must not start health checks for synthetic states.
    window.timer.stop()
    window.show()
    captures = []

    def settle():
        for _ in range(8):
            app.processEvents()
        window.refresh()
        stop = window.header.stop_button
        if not (stop.isVisible() and stop.isEnabled()
                and window.header.rect().contains(stop.geometry())
                and window.rect().contains(stop.mapTo(window, stop.rect().bottomRight()))):
            raise RuntimeError('Stop all is outside the visible workspace.')

    def capture(widget, key, mode, size):
        name = f'{mode}-{size}-{key}.png'
        pixmap = widget.grab()
        if not pixmap.save(str(directory / name)):
            raise RuntimeError('Could not write preview: ' + name)
        captures.append(dict(file=name, page=key, theme=mode, size=size,
            physical_pixels=list(pixmap.size().toTuple()), device_pixel_ratio=pixmap.devicePixelRatio()))

    try:
        for mode in ('light', 'dark'):
            theme.set_mode(mode, persist=False)
            for width, height in ((1280, 800), (800, 600), (640, 480)):
                window.resize(width, height)
                size = f'{width}x{height}'
                for spec in PAGES:
                    window.pages.set_current_key(spec.key)
                    settle()
                    capture(window, spec.key, mode, size)
            window.resize(1280, 800)
            window.sidebar.set_collapsed(True, persist=False)
            window.pages.set_current_key('layout')
            settle()
            capture(window, 'sidebar-collapsed', mode, '1280x800')
            window.sidebar.set_collapsed(False, persist=False)
            # Display-only synthetic states: no controller or physical device starts.
            setup = window.widgets['setup']
            name, path = setup.experiment_edit.text(), setup.directory_edit.text()
            setup.experiment_edit.setText('Experiment with a long name ł ' * 12)
            setup.directory_edit.setText(str(directory / ('long experiment folder ' * 8)))
            window._sync_context()
            window.services.states.update(board1='Running', camera='Stopping', trigger='Starting')
            try:
                for width, height in ((1280, 800), (640, 480)):
                    window.resize(width, height)
                    settle()
                    capture(window, 'header-long-active', mode, f'{width}x{height}')
            finally:
                window.services.states.update(board1='Stopped', camera='Stopped', trigger='Stopped')
                setup.experiment_edit.setText(name)
                setup.directory_edit.setText(path)
                window._sync_context()
            window.show_system_check()
            dialog = window._system_check_dialog
            dialog.resize(850, 650)
            settle()
            capture(dialog, 'system-check', mode, 'dialog')
            dialog.close()
            controls = QDialog(window)
            controls.setWindowTitle('Appearance validation: calendar and indicators')
            row = QVBoxLayout(controls)
            for enabled in (True, False):
                for checked in (False, True):
                    for cls in (QCheckBox, QRadioButton):
                        item = cls(f'{cls.__name__}: {"checked" if checked else "unchecked"}, '
                                   f'{"enabled" if enabled else "disabled"}')
                        item.setChecked(checked)
                        item.setEnabled(enabled)
                        row.addWidget(item)
            partial = QCheckBox('Partially checked')
            partial.setCheckState(Qt.PartiallyChecked)
            row.addWidget(partial)
            row.addWidget(ThemedCalendar())
            controls.show()
            settle()
            capture(controls, 'calendar-indicators', mode, 'dialog')
            controls.close()
            controls.deleteLater()

        # One compact image for each theme; original full-size screenshots remain available.
        for mode in ('light', 'dark'):
            sheet = QImage(1280, 660, QImage.Format_ARGB32)
            sheet.fill('#f4f6f6' if mode == 'light' else '#0e1414')
            painter = QPainter(sheet)
            painter.setFont(font(11))
            painter.setPen('#14201f' if mode == 'light' else '#e6eeed')
            for index, spec in enumerate(PAGES):
                x, y = (index % 4) * 320, (index // 4) * 220
                painter.drawText(x + 8, y + 18, spec.title)
                image = QImage(str(directory / f'{mode}-1280x800-{spec.key}.png'))
                image = image.scaled(320, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                painter.drawImage(x, y + 20, image)
            painter.end()
            sheet.save(str(directory / f'{mode}-overview.png'))
        report = dict(version=__version__, scale=args.scale, fonts=list(app._deepecohab_fonts),
            widget_font=QFontInfo(window.widgets['setup'].font()).family(), screenshots=captures,
            qt_warnings=warnings, physical_hardware_tested=False,
            navigation=dict(page_keys=[spec.key for spec in PAGES],
                stop_visible_in_every_capture=True, min_log_height=40,
                sidebar_widths=[64,232], automatic_compact_below=window.sidebar.COMPACT_BELOW))
        (directory / 'preview_results.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        content = ['<!doctype html><meta charset="utf-8"><title>Acquisition theme review</title>',
            '<style>body{font:16px system-ui;margin:24px;background:#eee;color:#14201f} '
            'article{margin:30px 0}img{max-width:100%;border:1px solid #bbb}a{color:#146a79}</style>',
            f'<h1>Acquisition {__version__}: display scale {args.scale:g}</h1>',
            '<p>Static previews. No physical devices were opened.</p>']
        for entry in captures:
            name = html.escape(entry['file'])
            content.append(f'<article><h2>{html.escape(entry["theme"] + " / " + entry["page"] + " / " + entry["size"])}</h2>'
                           f'<a href="{name}"><img src="{name}" loading="lazy"></a></article>')
        (directory / 'index.html').write_text('\n'.join(content), encoding='utf-8')
        print(json.dumps(dict(screenshots=len(captures), fonts=report['fonts'], scale=args.scale,
                             qt_warnings=len(warnings), output=str(directory))))
    finally:
        window.close()
        qInstallMessageHandler(None)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
