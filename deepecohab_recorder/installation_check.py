"""Explicit hardware-free release check; all sample data is temporary."""
import json
from pathlib import Path
import sys
import tempfile
import time

from . import __version__


def check_display_optimizations(window, app):
    """Exercise installed-only display paths without an acquisition device."""
    import copy
    import numpy as np
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    from .controllers.camera import CameraController
    from .models import CameraConfig

    window.resize(1280, 800)
    window.pages.set_current_key("layout")
    for _ in range(6):
        app.processEvents()
    layout = window.widgets["layout"]
    layout.set_mode("select")
    layout.fit()
    graph = copy.deepcopy(layout.read())
    view = layout.view
    start = next((QPoint(x, y) for y in range(20, view.viewport().height() - 20, 30)
        for x in range(20, view.viewport().width() - 20, 30) if view.hit(QPoint(x, y)) is None), None)
    if start is None:
        raise RuntimeError("Installed layout has no background for the pan check.")
    delta = QPoint(60, 40)
    before = view.mapFromScene(QPointF())
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=start)
    position = start + delta
    app.sendEvent(view.viewport(), QMouseEvent(QEvent.MouseMove, QPointF(position),
        QPointF(view.viewport().mapToGlobal(position)), Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=position)
    shift = view.mapFromScene(QPointF()) - before
    if abs(shift.x() - delta.x()) > 3 or abs(shift.y() - delta.y()) > 3 or layout.read() != graph:
        raise RuntimeError("Installed background panning failed or changed the graph.")
    layout.fit()

    window.pages.set_current_key("camera")
    for _ in range(6):
        app.processEvents()
    camera = window.widgets["camera"]
    controller = CameraController(CameraConfig())
    controller._preview_frame = np.full((64, 64), 53, dtype=np.uint8)
    controller._preview_sequence = 1
    camera.update_preview(controller)
    key = camera.preview_label.pixmap().cacheKey()
    camera.update_preview(controller)
    if key != camera.preview_label.pixmap().cacheKey():
        raise RuntimeError("Installed preview did not reuse its unchanged image.")
    for index in range(1000):
        window.services.messages.put(("info", f"Installation display check {index}"))
    window.refresh()
    if window.services.messages.empty():
        raise RuntimeError("Installed display burst did not yield between batches.")
    deadline = time.monotonic() + 10
    while not window.services.messages.empty() and time.monotonic() < deadline:
        app.processEvents()
    if not window.services.messages.empty() or "Installation display check 999" not in window.log.toPlainText():
        raise RuntimeError("Installed notification continuation lost messages.")
    return dict(background_pan_after_fit=True, graph_unchanged=True,
        unchanged_preview_cached=True, notification_burst_yields=True,
        notifications_retained=True)


def check_installation(window):
    from PySide6.QtCore import QProcess, QTimer
    from PySide6.QtGui import QFontInfo, QPalette
    from PySide6.QtWidgets import QApplication
    from .preprocessing.models import InputFile, Settings
    from .worker_process import start_preprocessing
    from .runtime_paths import worker_python

    app = QApplication.instance()
    window.show()
    app.processEvents()
    from .preferences import read_preferences
    from .ui.theme import TOKENS, icon
    original_theme = window.theme.mode
    from .ui.registry import PAGES
    page_ids = [id(window.pages.page(spec.key)) for spec in PAGES]
    appearance = dict(fonts=list(app._deepecohab_fonts), modes=[], icons_packaged=True,
                      preferences_separate_from_experiment=True)
    if appearance['fonts'] != ['Geist', 'Geist Mono']:
        raise RuntimeError('Bundled desktop fonts are missing: ' + str(appearance['fonts']))
    window.log.ensurePolished()
    if QFontInfo(window.log.font()).family() != 'Geist Mono':
        raise RuntimeError('The recording log is not using the bundled monospace font.')
    try:
        for mode in ('dark', 'light'):
            window.theme_selector.setCurrentIndex(window.theme_selector.findData(mode))
            app.processEvents()
            if window.theme.effective != mode or read_preferences()['appearance']['theme'] != mode:
                raise RuntimeError('Installed appearance preference did not update.')
            if app.palette().color(QPalette.Window).name() != TOKENS[mode]['ground']:
                raise RuntimeError('Installed theme palette did not update.')
            if icon('users').isNull() or any(window.sidebar.buttons[spec.key].icon().isNull() for spec in PAGES):
                raise RuntimeError('Installed SVG icons are unavailable.')
            if page_ids != [id(window.pages.page(spec.key)) for spec in PAGES]:
                raise RuntimeError('Theme switch replaced acquisition pages.')
            appearance['modes'].append(mode)
    finally:
        window.theme.set_mode(original_theme)
    pages = []
    for spec in PAGES:
        window.pages.set_current_key(spec.key)
        app.processEvents()
        window.refresh()
        pages.append(spec.title)
    navigation = dict(page_keys=[spec.key for spec in PAGES], sidebar_order=list(window.sidebar.order),
        mounted_once=True, stop_always_visible=True, compact_picker=True)
    original_size = window.size()
    original_collapsed = window.sidebar.manual_collapsed
    original_page = window.pages.current_key()
    try:
        for width, height in ((640,480),(1280,800)):
            window.resize(width,height)
            for compact in (True,False):
                window.sidebar.set_collapsed(compact, persist=False)
                for spec in PAGES:
                    window.pages.set_current_key(spec.key)
                    for _ in range(4):
                        app.processEvents()
                    stop = window.header.stop_button
                    if not (stop.isVisible() and stop.isEnabled()
                            and window.rect().contains(stop.mapTo(window, stop.rect().bottomRight()))):
                        raise RuntimeError('Installed Stop all is not visible on ' + spec.key)
                    if page_ids != [id(window.pages.page(page.key)) for page in PAGES]:
                        raise RuntimeError('Installed navigation replaced a page.')
        window.resize(640,480)
        app.processEvents()
        window.sidebar.toggle.click()
        app.processEvents()
        if not window.sidebar.menu.isVisible():
            raise RuntimeError('Installed compact page picker is unavailable.')
        window.sidebar.menu_actions['setup'].trigger()
        window.sidebar.menu.close()
        if window.pages.current_key() != 'setup':
            raise RuntimeError('Installed compact page selection did not navigate.')
    finally:
        window.sidebar.menu.close()
        window.sidebar.set_collapsed(original_collapsed, persist=False)
        window.resize(original_size)
        window.pages.set_current_key(original_page)
    display = check_display_optimizations(window, app)
    jobs = []
    with tempfile.TemporaryDirectory(prefix="acquisition_install_check_") as temp:
        root = Path(temp)
        raw = root / "synthetic.csv"
        raw.write_text(
            "A-1;0000A819;0;2025-06-01T12:00:00+02:00;1\n"
            "B-1;0000A819;A;2025-06-01T12:00:00.100+02:00;1\n", encoding="utf-8",
        )
        settings = Settings(
            inputs=[InputFile(str(raw), "COM1", 1, "v1.5", "1", tick_ms=10)],
            output_dir=str(root / "output"), whitelist=False, interpolate=False,
            parquet=True, csv=True,
        )
        path = root / "settings.json"
        path.write_text(json.dumps(settings.to_dict()), encoding="utf-8")
        for extra, expected, code in ((["--inventory"], "inventory_completed", 0),
                                      ([], "completed", 0), ([], "cancelled", 2)):
            if expected == "cancelled":
                (root / "cancel").touch()
            process = QProcess()
            start_preprocessing(process, ["--settings", str(path), "--cancel-file", str(root / "cancel"), *extra], root)
            if not process.waitForFinished(30_000):
                process.kill()
                process.waitForFinished(3000)
                raise RuntimeError("Installation check worker did not finish within 30 seconds.")
            stdout = bytes(process.readAllStandardOutput()).decode("utf-8")
            stderr = bytes(process.readAllStandardError()).decode("utf-8", errors="replace")
            messages = [json.loads(line) for line in stdout.splitlines() if line.strip()]
            if process.exitCode() != code or not any(m.get("kind") == expected for m in messages):
                raise RuntimeError(f"Worker failed: {stdout}\n{stderr}")
            jobs.append({"kind": expected, "exit_code": process.exitCode(), "messages": len(messages)})
        window.widgets['setup'].directory_edit.setText(str(root))
        window._sync_context()
        window.show_system_check()
        dialog = window._system_check_dialog
        dialog.encode_test.setChecked(False)
        ticks = []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start(10)
        dialog.run_checks()
        deadline = time.monotonic() + 50
        try:
            while dialog.background_busy() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.005)
            if dialog.background_busy() or dialog._report is None:
                raise RuntimeError('Installed System check failed: ' + dialog.status.text())
            if not dialog._report['core_ready']:
                raise RuntimeError('Installed core libraries/output failed: ' + dialog.status.text())
            system_check = dict(core_ready=dialog._report['core_ready'],
                software_version=dialog._report['software_version'],
                capabilities=dialog._report['capabilities'],
                progress_timer_ticks=len(ticks), report_rows=dialog.table.rowCount(),
                help_packaged=True, physical_acquisition_started=dialog._report['physical_acquisition_started'])
            if not ticks:
                raise RuntimeError('System check blocked the Qt event loop.')
        finally:
            timer.stop()
            dialog.request_background_stop()
            deadline = time.monotonic() + 5
            while dialog.background_busy() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            dialog.close()
    return {
        "version": __version__, "package": str(Path(__file__).resolve().parent),
        "python": sys.executable, "window_title": window.windowTitle(),
        "worker_python": worker_python(), "console_stdout_available": sys.stdout is not None,
        "window_icon_present": not window.windowIcon().isNull(),
        "pages": pages, "workers": jobs, "physical_hardware_tested": False,
        "system_check": system_check,
        "appearance": appearance,
        "navigation": navigation,
        "display_optimizations": display,
    }
