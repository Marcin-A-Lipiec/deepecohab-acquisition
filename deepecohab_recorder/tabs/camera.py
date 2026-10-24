from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
    QSplitter,
    QSizePolicy,
)
from deepecohab_recorder.ui.wheel_scroll import (
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
)

from deepecohab_recorder.controllers.camera import ENCODING_OPTIONS
from deepecohab_recorder.models import CameraConfig
from dataclasses import asdict
from deepecohab_recorder.ui.common import scroll
from deepecohab_recorder.ui.camera_preview import PreviewCache
from deepecohab_recorder.ui.theme import colors, manager


class CameraControlTab(QWidget):
    def __init__(self, app, parent=None) -> None:
        super().__init__(parent)
        self.ui = app
        root = QHBoxLayout(self)

        self.settings_group = QGroupBox("Camera recording settings")
        form = QFormLayout(self.settings_group)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(64, 10000)
        self.width_spin.setValue(1280)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(64, 10000)
        self.height_spin.setValue(1280)
        resolution = QWidget()
        rlay = QHBoxLayout(resolution)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.addWidget(self.width_spin)
        rlay.addWidget(QLabel("×"))
        rlay.addWidget(self.height_spin)
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 240)
        self.fps_spin.setValue(30)
        self.segment_spin = QSpinBox()
        self.segment_spin.setRange(60, 86400)
        self.segment_spin.setValue(3600)
        self.segment_spin.setSuffix(" s")
        self.encoding_combo = QComboBox()
        self.encoding_combo.addItems(list(ENCODING_OPTIONS.keys()))
        self.software_resize_check = QCheckBox("Enable")
        self.preview_check = QCheckBox("Enable")
        self.preview_check.setChecked(True)
        self.preview_fps_spin = QSpinBox()
        self.preview_fps_spin.setRange(1, 30)
        self.preview_fps_spin.setValue(10)
        self.auto_fallback_check = QCheckBox("Enable")
        self.auto_fallback_check.setChecked(True)
        self.auto_fallback_check.setToolTip(
            "Automatically use CPU H.264 after repeated NVENC failures."
        )
        self.encoding_combo.setMinimumContentsLength(14)
        self.encoding_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        self.width_spin.setMaximumWidth(90)
        self.height_spin.setMaximumWidth(90)
        self.fallback_failures_spin = QSpinBox()
        self.fallback_failures_spin.setRange(1, 20)
        self.fallback_failures_spin.setValue(3)
        form.addRow("Resolution:", resolution)
        form.addRow("Recording FPS:", self.fps_spin)
        form.addRow("Segment length:", self.segment_spin)
        form.addRow("Encoding:", self.encoding_combo)
        form.addRow("Software resize:", self.software_resize_check)
        form.addRow("Preview:", self.preview_check)
        form.addRow("Preview max FPS:", self.preview_fps_spin)
        form.addRow("CPU fallback:", self.auto_fallback_check)
        form.addRow("Fallback after failures:", self.fallback_failures_spin)

        self.trigger_group = QGroupBox("External camera-trigger configuration")
        tform = QFormLayout(self.trigger_group)
        tform.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.trigger_source_combo = QComboBox()
        self.trigger_source_combo.addItems(["Line0", "Line1", "Line2", "Line3"])
        self.trigger_activation_combo = QComboBox()
        self.trigger_activation_combo.addItems(["RisingEdge", "FallingEdge"])
        self.trigger_delay_spin = QDoubleSpinBox()
        self.trigger_delay_spin.setRange(0, 1000000)
        self.trigger_delay_spin.setDecimals(1)
        self.trigger_delay_spin.setValue(24.0)
        self.trigger_delay_spin.setSuffix(" µs")
        self.trigger_overlap_combo = QComboBox()
        self.trigger_overlap_combo.addItems(["ReadOut", "Off", "PreviousFrame"])
        tform.addRow("Trigger source:", self.trigger_source_combo)
        tform.addRow("Activation:", self.trigger_activation_combo)
        tform.addRow("Delay:", self.trigger_delay_spin)
        tform.addRow("Overlap:", self.trigger_overlap_combo)

        left = QVBoxLayout()
        left.addWidget(self.settings_group)
        left.addWidget(self.trigger_group)
        left.addStretch(1)
        controls = QWidget()
        controls.setLayout(left)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(scroll(controls))
        root.addWidget(self.splitter)
        for group in (self.settings_group, self.trigger_group):
            group.setCheckable(True)
            group.setChecked(True)
            group.toggled.connect(
                lambda on, group=group: [
                    group.layout().itemAt(i).widget().setVisible(on)
                    for i in range(group.layout().count())
                    if group.layout().itemAt(i).widget()
                ]
            )

        preview_group = QGroupBox("Live preview")
        preview_layout = QVBoxLayout(preview_group)
        self.preview_label = QLabel("Camera not running")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(260, 260)
        self.preview_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.apply_theme()
        manager().changed.connect(self.apply_theme)
        self.status_label = QLabel("Camera stopped")
        self.status_label.setWordWrap(True)
        preview_layout.addWidget(self.preview_label, 1)
        preview_layout.addWidget(self.status_label)
        self.splitter.addWidget(preview_group)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([355, 825])
        self._preview_cache = PreviewCache()
        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._preview_tick)

    def apply_theme(self, *args):
        c = colors()
        self.preview_label.setStyleSheet(
            f'background:{c["preview"]}; color:{c["preview-ink"]}; '
            f'border:1px solid {c["line"]};'
        )

    def set_running_state(self, running: bool) -> None:
        self.settings_group.setEnabled(not running)
        self.trigger_group.setEnabled(not running)

    def config(self) -> CameraConfig:
        return CameraConfig(
            width=self.width_spin.value(),
            height=self.height_spin.value(),
            fps=self.fps_spin.value(),
            segment_seconds=self.segment_spin.value(),
            preview_enabled=self.preview_check.isChecked(),
            preview_fps=self.preview_fps_spin.value(),
            software_resize=self.software_resize_check.isChecked(),
            encoding=self.encoding_combo.currentText(),
            trigger_source=self.trigger_source_combo.currentText(),
            trigger_activation=self.trigger_activation_combo.currentText(),
            trigger_delay_us=self.trigger_delay_spin.value(),
            trigger_overlap=self.trigger_overlap_combo.currentText(),
            auto_cpu_fallback=self.auto_fallback_check.isChecked(),
            fallback_after_failures=self.fallback_failures_spin.value(),
        )

    def apply_config(self, data: dict) -> None:
        if not isinstance(data, dict):
            return
        self.width_spin.setValue(
            int(data.get("width") or data.get("target_width") or 1280)
        )
        self.height_spin.setValue(
            int(data.get("height") or data.get("target_height") or 1280)
        )
        self.fps_spin.setValue(int(data.get("fps") or 30))
        self.segment_spin.setValue(
            int(data.get("segment_seconds") or data.get("segment_time_s") or 3600)
        )
        encoding = str(data.get("encoding") or "DeepEcoHAB_default (NVENC HQ)")
        if self.encoding_combo.findText(encoding) >= 0:
            self.encoding_combo.setCurrentText(encoding)
        self.software_resize_check.setChecked(
            bool(
                data.get("software_resize")
                or data.get("software_resize_if_needed", False)
            )
        )
        self.preview_check.setChecked(bool(data.get("preview_enabled", True)))
        self.preview_fps_spin.setValue(
            int(data.get("preview_fps") or data.get("preview_max_fps") or 10)
        )
        self.trigger_source_combo.setCurrentText(
            str(data.get("trigger_source") or "Line0")
        )
        self.trigger_activation_combo.setCurrentText(
            str(data.get("trigger_activation") or "RisingEdge")
        )
        self.trigger_delay_spin.setValue(float(data.get("trigger_delay_us", 24.0)))
        self.trigger_overlap_combo.setCurrentText(
            str(data.get("trigger_overlap") or "ReadOut")
        )
        self.auto_fallback_check.setChecked(bool(data.get("auto_cpu_fallback", True)))
        self.fallback_failures_spin.setValue(
            int(data.get("fallback_after_failures") or 3)
        )

    def update_preview(self, controller) -> None:
        if controller is None:
            return
        self.status_label.setText(controller.status_text())
        self._preview_cache.update(controller, self.preview_label)

    def _preview_tick(self):
        self.update_preview(self.ui.services.controllers.get("camera"))

    def _sync_preview_timer(self, controller):
        config = getattr(controller, "config", None)
        enabled = config.preview_enabled if config is not None else self.preview_check.isChecked()
        fps = config.preview_fps if config is not None else self.preview_fps_spin.value()
        if not (self.isVisible() and controller is not None and enabled):
            self._preview_timer.stop()
            return
        interval = max(1, round(1000 / max(1, min(30, fps))))
        if not self._preview_timer.isActive() or self._preview_timer.interval() != interval:
            self._preview_timer.start(interval)

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_preview_timer(self.ui.services.controllers.get("camera"))

    def hideEvent(self, event):
        self._preview_timer.stop()
        super().hideEvent(event)

    paths = ("acquisition.camera",)
    hardware = True

    def read(self):
        return {"acquisition.camera": asdict(self.config())}

    def load(self, data):
        self.apply_config(data["acquisition"]["camera"])

    def reset_experiment_display(self):
        self._preview_timer.stop()
        self._preview_cache = PreviewCache()
        self.preview_label.clear()
        self.status_label.setText('Ready')

    def refresh(self):
        controller = self.ui.services.controllers.get("camera")
        self.set_running_state(self.ui.services.busy("camera"))
        self._sync_preview_timer(controller)
        self.update_preview(controller)
