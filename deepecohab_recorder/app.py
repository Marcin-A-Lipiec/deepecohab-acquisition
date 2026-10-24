"""Console and windowed entry points, with durable startup errors."""
from __future__ import annotations

import argparse
from importlib import resources
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import threading

from . import APP_NAME, SOFTWARE_LABEL, __version__


def configure_logging():
    from .runtime_paths import log_directory
    directory = log_directory()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "application.log"
    logger = logging.getLogger("deepecohab_recorder")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
    return path


def show_startup_error(message):
    if sys.stderr is not None:
        print(message, file=sys.stderr)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--smoke-test", type=Path, help=argparse.SUPPRESS)
    args, qt_args = parser.parse_known_args(argv)
    if args.version:
        if sys.stdout is not None:
            print(SOFTWARE_LABEL)
        return 0
    log_path = None
    window = None
    old_hook, old_thread_hook = sys.excepthook, threading.excepthook
    logger = logging.getLogger(__name__)
    try:
        log_path = configure_logging()
        logger.info("Starting %s with %s", SOFTWARE_LABEL, sys.executable)
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "DeepEcoHab.Acquisition.Preview" if os.environ.get('DEEPECOHAB_ACQUISITION_PREVIEW') == '1' else "DeepEcoHab.Acquisition")

        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication, QMessageBox
        from .main_window import MainWindow
        from .runtime_paths import asset
        from .ui.theme import manager

        app = QApplication.instance() or QApplication([sys.argv[0], *qt_args])
        app.setApplicationName(APP_NAME)
        app.setOrganizationName("DeepEcoHab")
        app.setApplicationVersion(__version__)
        with resources.as_file(asset("acquisition.ico")) as icon_path:
            app.setWindowIcon(QIcon(str(icon_path)))
        manager()

        def unhandled(kind, value, tb):
            logger.error("Unhandled GUI exception", exc_info=(kind, value, tb))
            if args.smoke_test:
                app.exit(1)
            else:
                QMessageBox.critical(window, APP_NAME, f"{value}\n\nDetails: {log_path}")

        sys.excepthook = unhandled
        threading.excepthook = lambda event: logger.error(
            "Unhandled background exception in %s", event.thread.name,
            exc_info=(event.exc_type, event.exc_value, event.exc_traceback),
        )
        window = MainWindow()
        if os.environ.get('DEEPECOHAB_ACQUISITION_PREVIEW') == '1':
            window.setWindowTitle(SOFTWARE_LABEL + ' — TEST')
        window.show()
        if args.smoke_test:
            def check():
                result, code = {}, 0
                try:
                    from .installation_check import check_installation
                    result = check_installation(window)
                except Exception as exc:
                    logger.exception("Installation check failed")
                    result, code = {"error": str(exc)}, 1
                finally:
                    try:
                        args.smoke_test.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
                    except OSError:
                        logger.exception("Cannot write installation check result")
                        code = 1
                    window.close()
                    app.exit(code)
            QTimer.singleShot(0, check)
        return app.exec()
    except Exception as exc:
        logger.exception("Application startup failed")
        message = f"{APP_NAME} could not start.\n\n{exc}\n\nLog: {log_path or 'unavailable'}"
        if args.smoke_test:
            args.smoke_test.write_text(json.dumps({"error": message}) + "\n", encoding="utf-8")
        else:
            show_startup_error(message)
        return 1
    finally:
        sys.excepthook, threading.excepthook = old_hook, old_thread_hook


if __name__ == "__main__":
    raise SystemExit(main())
