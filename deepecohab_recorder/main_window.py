"""Application composition, generic config actions and GUI-thread notifications."""

from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QSignalBlocker
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from deepecohab_recorder.ui.wheel_scroll import (
    QComboBox,
)
from deepecohab_recorder import APP_NAME, SOFTWARE_LABEL
from deepecohab_recorder.config import get_path, set_path
from deepecohab_recorder.schema import CONFIG_VERSION
from deepecohab_recorder.config_bundle import load_config_bundle, bundle_digest
from deepecohab_recorder.event_metadata import compartment_names
from deepecohab_recorder.services.application import ApplicationServices
from deepecohab_recorder.ui.common import scroll
from deepecohab_recorder.ui.context import UiContext
from deepecohab_recorder.ui.notifications import NotificationPump
from deepecohab_recorder.ui.registry import PAGES
from deepecohab_recorder.ui.shell import ExperimentHeader, NavigationSidebar, PageStack, PageViewport, navigation_shortcuts
from deepecohab_recorder.ui.theme import manager, icon
from deepecohab_recorder.utils import read_json


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.theme = manager()
        self.setWindowTitle(SOFTWARE_LABEL)
        self.setMinimumSize(640, 480)
        screen = self.screen().availableGeometry()
        self.resize(
            min(1450, round(screen.width() * 0.93)),
            min(950, round(screen.height() * 0.9)),
        )
        self.services = ApplicationServices()
        self.notifications = NotificationPump(self.services.messages,
            lambda: self.services.context.timezone, self._display_notifications, self)
        self.ui = UiContext(self.services)
        self.ui.bind(
            load_section=self.load_section,
            load_all=self.load_all,
            save_all=self.save_all,
            background_busy=self._background_busy,
            preprocessing_sources=lambda: self.widgets["preprocessing"].source_specs(),
            preprocessing_config_updated=self.preprocessing_config_updated,
            experiment_timezone_changed=self.experiment_timezone_changed,
            save_section=self.save_section,
            hardware_settings=self.hardware_settings,
            start_devices=self.start_devices,
            stop_devices=self.stop_devices,
            directory_changed=self.directory_changed,
            experiment_name_changed=self.experiment_name_changed,
            prepare_matching=self.prepare_matching,
            experiment_timezone=self.experiment_timezone,
            layout_state=lambda: self.widgets["layout"].read(),
            layout_compartments=lambda: compartment_names(self.widgets['layout'].graph),
        )
        self.widgets = {}
        self._system_check_dialog = None
        self._directory, self._closing = None, False
        self.header = ExperimentHeader(self)
        for label, symbol, callback, shortcut in [
            ("Load experiment configuration", "folders", self.load_all, "Ctrl+O"),
            ("Save experiment configuration", "download", self.save_all, "Ctrl+S"),
            ("Check configuration", "circle-check", self.check_config, None),
            ("System check", "info-circle", self.show_system_check, None),
        ]:
            self.header.add_action(label, symbol, callback, shortcut)
        self.header.stop_button.clicked.connect(lambda checked=False: self.stop_devices())
        self.config_status = self.header.config_status
        self.config_status.setText(f"Configuration v{CONFIG_VERSION} · Draft")
        self.statusBar().addPermanentWidget(QLabel("Theme:"))
        self.theme_selector = QComboBox()
        self.theme_selector.setObjectName("themeSelector")
        self.theme_selector.setAccessibleName("Appearance theme")
        self.theme_selector.setToolTip("System follows your desktop's light or dark preference.")
        for label, value in (("System", "system"), ("Light", "light"), ("Dark", "dark")):
            self.theme_selector.addItem(label, value)
        self.theme_selector.setCurrentIndex(self.theme_selector.findData(self.theme.mode))
        self.theme_selector.currentIndexChanged.connect(self._select_theme)
        self.statusBar().addPermanentWidget(self.theme_selector)
        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        self.pages = PageStack(PAGES)
        self.pages.setMinimumHeight(80)
        # Retain numeric access for external callers; no horizontal tabs are built.
        self.tabs = self.pages
        for spec in PAGES:
            tab = spec.factory(self.ui)
            self.widgets[spec.key] = tab
            self.pages.addWidget(scroll(tab) if spec.scrollable else PageViewport(tab))
        splitter.addWidget(self.pages)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setPlaceholderText("Recording status and errors appear here.")
        self.log.setMinimumHeight(40)
        self.log.setAccessibleName("Acquisition activity and errors")
        splitter.addWidget(self.log)
        splitter.setSizes([760, 95])
        splitter.setStretchFactor(0, 1)
        root = QWidget()
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.sidebar = NavigationSidebar(PAGES)
        row.addWidget(self.sidebar)
        workspace = QVBoxLayout()
        workspace.setContentsMargins(0, 0, 0, 0)
        workspace.setSpacing(0)
        workspace.addWidget(self.header)
        workspace.addWidget(splitter, 1)
        row.addLayout(workspace, 1)
        self.setCentralWidget(root)
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._resize_tabs)
        self.pages.resized.connect(lambda: self._resize_timer.start(0))
        self.sidebar.page_requested.connect(self.pages.set_current_key)
        self.sidebar.preference_error.connect(self._navigation_error)
        self.pages.currentChanged.connect(self._page_changed)
        self._navigation_shortcuts = navigation_shortcuts(self, self.sidebar, self.pages)
        for widget in self.widgets.values():
            if hasattr(widget, "process") and hasattr(widget, "background_busy"):
                widget.process.stateChanged.connect(lambda state: self._refresh_header())
        self.sidebar.update_available_width(self.width())
        self.theme.changed.connect(self._theme_changed)
        self.theme.mode_changed.connect(self._theme_mode_changed)
        self._theme_changed()
        self._apply(self.services.repo.data)
        self.widgets["acquisition"].refresh_ports()
        self._sync_context()
        self._page_changed(self.pages.currentIndex())
        self._resize_tabs()
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        if self.theme.preference_error:
            self.log.appendPlainText(f"Appearance: {self.theme.preference_error}")
            self.statusBar().showMessage("Appearance settings unavailable; using System theme.", 10000)
        if self.sidebar.startup_error:
            self._navigation_error(self.sidebar.startup_error)

    def _navigation_error(self, text):
        self.log.appendPlainText(f"Navigation settings: {text}")
        self.statusBar().showMessage("Sidebar settings could not be saved; see the activity log.", 10000)

    def _page_changed(self, index):
        spec = PAGES[index]
        self.sidebar.select(spec.key)
        self.header.page_title.setText(spec.title)
        self._resize_timer.start(0)
        self.refresh()

    def _refresh_header(self):
        services = self.services
        owned = services.ports.snapshot()
        if services.busy() or owned or services.lighting.busy:
            name, directory = services.context.experiment_name, services.context.save_directory
        else:
            name, directory = self.widgets["setup"].context_values()
        self.header.set_identity(name or "Unnamed experiment", str(directory))
        names = {"board1": "RFID board 1", "board2": "RFID board 2", "camera": "Camera", "trigger": "Hardware trigger"}
        statuses = [f"{names.get(key, key)}: {value.lower()}" for key, value in services.states.items() if value != "Stopped"]
        matching = [port for port, owner in owned.items() if owner == "tag matching"]
        if matching:
            statuses.append("Tag matching: " + ", ".join(matching))
        if services.lighting.busy:
            statuses.append("Lighting: communicating")
        elif services.lighting.connected:
            statuses.append("Lighting: connected")
        if self._tab_background_busy():
            statuses.append("Preprocessing task running")
        if self.system_check_busy():
            statuses.append("System check running")
        failed = any(value not in ("Stopped", "Starting", "Running", "Stopping") for value in services.states.values())
        self.header.set_activity(" · ".join(statuses) if statuses else "All acquisition devices stopped", "error" if failed else "muted")

    def _select_theme(self):
        try:
            self.theme.set_mode(self.theme_selector.currentData())
        except (ValueError, OSError) as exc:
            self._theme_mode_changed(self.theme.mode)
            self.log.appendPlainText(f"Could not save appearance: {exc}")
            self.statusBar().showMessage(f"Could not save appearance: {exc}", 10000)

    def _theme_mode_changed(self, mode):
        with QSignalBlocker(self.theme_selector):
            self.theme_selector.setCurrentIndex(self.theme_selector.findData(mode))

    def _theme_changed(self, *args):
        for index, spec in enumerate(PAGES):
            self.pages.setTabIcon(index, icon(spec.symbol))
        self.sidebar.apply_theme()
        self.header.apply_theme()

    def experiment_timezone(self):
        setup = self.widgets.get("setup")
        return (
            setup.zone.currentText().strip() if setup else "Europe/Warsaw"
        )

    def preprocessing_config_updated(self, path, config, layout_changed=False):
        """Update the current experiment without discarding other unsaved fields."""
        current_path = self.services.repo.discover()
        if current_path is None or Path(current_path).resolve() != Path(path).resolve():
            return
        self.services.repo._digests[str(Path(path).resolve())] = bundle_digest(path)
        updates = {'recording.timeline': config['recording']['timeline'],
                   'acquisition._provenance': config['acquisition'].get('_provenance', {}),
                   'acquisition.active_inputs': config['acquisition'].get('active_inputs', {})}
        if layout_changed:
            updates['recording.layout'] = config['recording']['layout']
            updates['recording.devices'] = config['recording'].get('devices', [])
            updates['acquisition.preprocessing'] = config['acquisition']['preprocessing']
        self.services.repo.update_paths(updates)
        if layout_changed:
            self.widgets['layout'].load(config)
        # Keep a user's unsaved manual edits; automatic displays refresh live.
        setup = self.widgets['setup']
        if not setup.manual_times.isChecked():
            setup.load_times(config['recording']['timeline'])

    def experiment_timezone_changed(self, *args):
        try:
            self._sync_context()
            tab = self.widgets["preprocessing"]
            tab.options["timezone"] = self.experiment_timezone()
            tab.scan_directory(only_changed=True)
        except Exception as exc:
            self._error(exc)

    def hardware_settings(self):
        config = self.services.repo.data
        for widget in self.widgets.values():
            if getattr(widget, "hardware", False):
                for path, value in widget.read().items():
                    set_path(config, path, value)
        return config["acquisition"]

    def _sync_context(self):
        name, directory = self.widgets["setup"].context_values()
        if not directory:
            raise ValueError("Choose an Experiment's save directory.")
        self.services.set_context(name, directory, self.experiment_timezone())
        self._refresh_header()

    def _base_for_save(self):
        self._sync_context()
        directory = str(self.services.repo.paths()[1].parent)
        if self._directory != directory and not self.services.busy():
            path = self.services.repo.discover()
            if path is not None:
                self.services.repo.import_file(path)
            self._directory = directory

    def _apply(self, config, selected=None):
        for widget in self.widgets.values():
            if selected is None or widget is selected:
                widget.load(config)

    def _error(self, error):
        self.log.appendPlainText(f"ERROR: {error}")
        QMessageBox.warning(self, APP_NAME, str(error))

    def _legacy_notice(self, source):
        if 'missing' in self.services.repo.notice.lower():
            QMessageBox.information(self, 'Acquisition settings unavailable', self.services.repo.notice)
        if (
            source.get("version") != CONFIG_VERSION
            or "recording" not in source
            or "acqusition" in source
        ):
            text = f"Loaded older config format (version {source.get('version', 'unversioned')}). The current format is version {CONFIG_VERSION}. Save to {self.services.repo.paths()[1].name} to update it."
            self.config_status.setText(text)
            QMessageBox.information(self, "Older configuration loaded", text)

    def experiment_name_changed(self):
        try:
            self._sync_context()
            self._directory = str(self.services.repo.paths()[1].parent)
            self._directory_identity = (self._directory, self.services.context.experiment_name)
            self.config_status.setText(f"Save target: {self.services.repo.paths()[1].name}.")
            self.widgets["preprocessing"].configure_experiment(force=True)
        except Exception as exc:
            self._error(exc)

    def directory_changed(self):
        try:
            self._sync_context()
            directory = str(self.services.repo.paths()[1].parent)
            identity = (directory, self.widgets["setup"].context_values()[0])
            if identity == getattr(self, "_directory_identity", None):
                return
            self._directory_identity = identity
            self._directory = directory
            path = self.services.repo.discover()
            if path is not None:
                source = read_json(path)
                config = self.services.repo.import_file(path)
                self._apply(config)
                self._sync_context()
                self._legacy_notice(source)
                self.config_status.setText(f"Loaded {path}")
            else:
                tab = self.widgets["preprocessing"]
                draft = self.services.repo.data
                draft['acquisition']['preprocessing'] = {'sources': [], 'options': {}}
                tab.load(draft)
                self.config_status.setText(
                    f"No experiment configuration found. Save target: {self.services.repo.paths()[1].name}."
                )
            self.widgets["preprocessing"].configure_experiment(force=True)
        except Exception as exc:
            self._error(exc)

    def _choose_config(self):
        self._sync_context()
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load experiment configuration",
            str(self.services.repo.paths()[1]),
            "JSON files (*.json)",
        )
        return Path(path) if path else None

    def load_all(self):
        if (
            self._background_busy()
            or self.services.busy()
            or self.services.ports.snapshot()
            or self.services.lighting.busy
        ):
            self._error(
                "Stop devices and finish tag matching or preprocessing before loading another experiment configuration."
            )
            return
        try:
            path = self._choose_config()
            if not path:
                return
            source = read_json(path)
            config = self.services.repo.import_file(path)
            self.widgets["setup"].directory_edit.setText(str(path.parent))
            self.services.run.detach()
            self._apply(config)
            self._sync_context()
            self._directory = str(self.services.repo.paths()[1].parent)
            self._legacy_notice(source)
            self.config_status.setText(
                f"Imported {path.name}; save target is {self.services.repo.paths()[1].name}."
            )
        except Exception as exc:
            self._error(exc)

    def load_section(self, widget):
        try:
            if any(path.startswith("recording.timeline") for path in widget.paths) and (
                self.services.busy()
                or self.services.lighting.connected
                or self.services.lighting.busy
            ):
                raise RuntimeError(
                    "Stop recording before changing its timezone or phase schedule."
                )
            path = self._choose_config()
            if not path:
                return
            source = read_json(path)
            config = load_config_bundle(path)
            updates = {key: get_path(config, key) for key in widget.paths}
            self.services.repo.update_paths(updates)
            widget.load(self.services.repo.data)
            self._legacy_notice(source)
        except Exception as exc:
            self._error(exc)

    def _save(self, updates):
        self._base_for_save()
        self.services.repo.update_paths({"acquisition": self.hardware_settings()})
        paths = self.services.run.save_metadata(updates)
        self.config_status.setText(f"Saved config v{CONFIG_VERSION}: {paths[1]}")
        self.log.appendPlainText(f"Saved {paths[1]}")
        self.widgets["preprocessing"].configure_experiment(force=True)
        return paths

    def save_section(self, widget):
        try:
            self._sync_context()
            self._save(widget.read())
            widget.load(self.services.repo.data)
        except Exception as exc:
            self._error(exc)

    def save_all(self):
        try:
            self._sync_context()
            updates = {}
            for widget in self.widgets.values():
                updates.update(widget.read())
            self._save(updates)
            self._apply(self.services.repo.data)
        except Exception as exc:
            self._error(exc)

    def check_config(self):
        try:
            self._sync_context()
            _, errors, notice = self.services.repo.preflight()
            QMessageBox.information(
                self,
                "Configuration check",
                (
                    "\n".join(errors)
                    if errors
                    else "The experiment configuration has complete animal details and a valid physical EcoHAB layout."
                    + ("\n" + notice if notice else "")
                ),
            )
        except Exception as exc:
            self._error(exc)

    def confirm_override(self, errors):
        box = QMessageBox(self)
        box.setWindowTitle("RFID recording without a complete experiment configuration")
        box.setIcon(QMessageBox.Warning)
        box.setText(
            "The Experiment's save directory has no compatible, complete experiment configuration. Continue RFID recording anyway?"
        )
        box.setInformativeText(
            "\n".join(errors[:8])
            + ("\nMore details are available below." if len(errors) > 8 else "")
        )
        box.setDetailedText("\n".join(errors))
        yes = box.addButton("Continue recording", QMessageBox.AcceptRole)
        no = box.addButton("Return to configuration", QMessageBox.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        return box.clickedButton() is yes

    def start_devices(self, keys):
        try:
            if not keys:
                return
            if self.system_check_busy():
                raise RuntimeError('Finish or cancel System check before starting acquisition.')
            self._sync_context()
            settings = self.hardware_settings()
            validated, override = None, False
            if any(key.startswith("board") for key in keys):
                # Metadata readiness is reviewed before preprocessing. Drafts
                # may record; override retains their journal without autosaving.
                validated, errors, notice = self.services.repo.preflight(
                    settings["boards"]
                )
                if errors:
                    override = True
                    self.log.appendPlainText("Recording with draft experiment metadata. Complete or review it before preprocessing.")
                elif notice:
                    QMessageBox.information(
                        self,
                        "Older configuration loaded",
                        notice + f"\nSave to update it to config v{CONFIG_VERSION}.",
                    )
                if validated is not None and not self.services.run.active:
                    name, directory = self.widgets["setup"].context_values()
                    zone = validated["recording"]["timeline"]["recording_timezone"]
                    self.services.set_context(name, directory, zone)
                    self.widgets["environment"].load(validated)
                    self.widgets["setup"].zone.setCurrentText(zone)
                    validated["recording"]["name"] = name
                if validated is None:
                    # Preserve GUI metadata as an in-memory draft and in the run journal.
                    self.services.repo.update_paths(
                        {
                            "recording.timeline.recording_timezone": self.experiment_timezone(),
                            "recording.name": self.widgets[
                                "setup"
                            ].context_values()[0],
                        }
                    )
            self.services.start_devices(keys, settings, validated, override)
        except Exception as exc:
            self._error(exc)

    def stop_devices(self, keys=None):
        self.services.stop_devices(keys)

    def prepare_matching(self):
        try:
            self._sync_context()
            return True
        except Exception as exc:
            self._error(exc)
            return False

    def _resize_tabs(self):
        for key, tab in self.widgets.items():
            if hasattr(tab, "viewport_size"):
                viewport = self.pages.page(key).viewport()
                tab.viewport_size(viewport.width(), viewport.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "sidebar"):
            self.sidebar.update_available_width(self.width())
            self._resize_timer.start(0)

    def refresh(self):
        # Hardware locks update on every tab; expensive live views refresh only when visible.
        current = self.pages.current_key()
        for spec in PAGES:
            widget = self.widgets[spec.key]
            if hasattr(widget, "refresh") and (
                spec.key == current or spec.key in ("setup", "acquisition", "environment")
            ):
                widget.refresh()
        self.notifications.drain()
        if self._system_check_dialog is not None:
            self._system_check_dialog.update_activity()
        self._refresh_header()
        if (
            self._closing
            and not self.services.busy()
            and not self.services.ports.snapshot()
            and not self.services.lighting.busy
            and not self._background_busy()
        ):
            self.services.shutdown()
            self.timer.stop()
            self.close()

    def _display_notifications(self, batch):
        self.log.appendPlainText(batch.lines)
        self.statusBar().showMessage(batch.last_message, 15000)
        if batch.last_error is not None:
            self.config_status.setText(batch.last_error)

    def _background_busy(self):
        return self.system_check_busy() or self._tab_background_busy()

    def _tab_background_busy(self):
        return any(
            widget.background_busy()
            for widget in self.widgets.values()
            if hasattr(widget, "background_busy")
        )

    def system_check_busy(self):
        return self._system_check_dialog is not None and self._system_check_dialog.background_busy()

    def show_system_check(self):
        from deepecohab_recorder.ui.system_check import SystemCheckDialog
        if self._system_check_dialog is None:
            def options():
                camera = self.widgets['camera']
                return dict(output_directory=self.widgets['setup'].context_values()[1],
                    encoding=camera.encoding_combo.currentText(),
                    auto_cpu_fallback=camera.auto_fallback_check.isChecked(),
                    owned_ports=self.services.ports.snapshot())
            def activity():
                return dict(camera=self.services.busy('camera'),
                    busy=bool(self.services.busy() or self.services.ports.snapshot()
                              or self.services.lighting.busy or self._tab_background_busy()))
            self._system_check_dialog = SystemCheckDialog(self, options, activity)
            self._system_check_dialog.process.stateChanged.connect(lambda state: self._refresh_header())
        self._system_check_dialog.update_activity()
        self._system_check_dialog.show()
        self._system_check_dialog.raise_()
        self._system_check_dialog.activateWindow()

    def closeEvent(self, event):
        if self.system_check_busy():
            self._system_check_dialog.request_background_stop()
            if not (self.services.busy() or self.services.ports.snapshot()
                    or self.services.lighting.busy or self._tab_background_busy()):
                self._closing = True
                event.ignore()
                return
        if (
            self.services.busy()
            or self.services.ports.snapshot()
            or self.services.lighting.busy
            or self._background_busy()
        ):
            event.ignore()
            if not self._closing:
                if (
                    QMessageBox.question(
                        self,
                        "Stop acquisition and close",
                        "Stop acquisition, cancel preprocessing, and close after files are released? Independently powered lighting and standalone lickometers will continue operating; their TTL events are not recorded after the RFID boards stop.",
                    )
                    != QMessageBox.Yes
                ):
                    return
                self._closing = True
                self.services.stop_devices()
                self.services.lighting.close()
                for widget in self.widgets.values():
                    if hasattr(widget, "request_background_stop"):
                        widget.request_background_stop()
                self.config_status.setText(
                    "Stopping devices and flushing data before closing…"
                )
            return
        self.services.shutdown()
        event.accept()
