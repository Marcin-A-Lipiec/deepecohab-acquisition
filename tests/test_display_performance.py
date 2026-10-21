"""Display optimizations preserve queued messages and camera buffer ownership."""
import io
import logging
import queue
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QTimer

from deepecohab_recorder.controllers.camera import CameraController
from deepecohab_recorder.models import CameraConfig
from deepecohab_recorder.ui.notifications import NotificationPump
from tests.test_navigation import workspace, settle, qt_wait


def test_notification_burst_yields_to_gui_and_preserves_fifo(workspace):
    app, window, root = workspace
    messages, batches, other_events = queue.Queue(), [], []
    for index in range(1050):
        messages.put(("error" if index == 211 else "info", str(index)))
    pump = NotificationPump(messages, lambda: "Europe/Warsaw", batches.append, window)
    QTimer.singleShot(0, lambda: other_events.append(len(batches)))
    pump.drain()
    assert not messages.empty()
    qt_wait(app, lambda: messages.empty() and bool(other_events))
    lines = [line for batch in batches for line in batch.lines.splitlines()]
    assert [line.split(": ", 1)[1] for line in lines] == [str(i) for i in range(1050)]
    assert other_events[0] < len(batches)
    assert [batch.last_error for batch in batches if batch.last_error is not None] == ["211"]
    assert batches[-1].last_message == "1049"


@pytest.mark.parametrize('display_limit', [1, 200])
def test_warning_and_error_are_logged_before_pending_display_is_drained(workspace, display_limit):
    app, window, root = workspace
    # One message per batch exercises continuation even on a fast test PC.
    window.notifications.limit = display_limit
    output = io.StringIO()
    logger = logging.getLogger("deepecohab_recorder.services.application")
    original_level = logger.level
    handler = logging.StreamHandler(output)
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        window.services.notify("error", "Synthetic writer failure")
        window.services.notify("warning", "Synthetic low disk space")
        assert "Synthetic writer failure" in output.getvalue()
        assert "Synthetic low disk space" in output.getvalue()
        window.refresh()
        assert window.config_status.text() == "Synthetic writer failure"
        if display_limit == 1:
            assert "Synthetic low disk space" not in window.log.toPlainText()
            assert window.notifications.timer.isActive()
        # Bounded display work may span several event-loop turns on a busy PC.
        qt_wait(app, lambda: "Synthetic low disk space" in window.log.toPlainText())
        displayed = window.log.toPlainText()
        assert displayed.index("Synthetic writer failure") < displayed.index("Synthetic low disk space")
        assert window.config_status.text() == "Synthetic writer failure"
    finally:
        logger.removeHandler(handler)
        logger.setLevel(original_level)


def test_preview_snapshot_owns_pixels_and_legacy_getter_remains_compatible():
    controller = CameraController(CameraConfig())
    controller._preview_frame = np.full((32, 32), 41, dtype=np.uint8)
    controller._preview_sequence = 1
    sequence, frame = controller.get_preview_snapshot()
    frame[:] = 255
    assert np.all(controller.get_preview_frame() == 41)
    assert controller.get_preview_snapshot(sequence) == (1, None)
    controller._preview_frame = np.full((32, 32), 99, dtype=np.uint8)
    controller._preview_sequence += 1
    sequence, frame = controller.get_preview_snapshot(sequence)
    assert sequence == 2 and np.all(frame == 99)


def test_preview_skips_unchanged_pixels_resizes_and_replaces_controller(workspace):
    app, window, root = workspace
    window.pages.set_current_key("camera")
    settle(app)
    tab = window.widgets["camera"]
    controller = CameraController(CameraConfig())
    controller._preview_frame = np.full((64, 64), 41, dtype=np.uint8)
    controller._preview_sequence = 1
    tab.update_preview(controller)
    original = tab.preview_label.pixmap().cacheKey()
    controller._preview_frame[:] = 99  # Unpublished source edits cannot alter GUI-owned pixels.
    for _ in range(10):
        tab.update_preview(controller)
    assert tab.preview_label.pixmap().cacheKey() == original
    assert tab.preview_label.pixmap().toImage().pixelColor(5, 5).red() == 41
    window.resize(1000, 700)
    settle(app)
    tab.update_preview(controller)
    assert tab.preview_label.pixmap().cacheKey() != original
    assert tab.preview_label.pixmap().toImage().pixelColor(5, 5).red() == 41
    controller._preview_sequence += 1
    tab.update_preview(controller)
    assert tab.preview_label.pixmap().toImage().pixelColor(5, 5).red() == 99
    replacement = CameraController(CameraConfig())
    replacement._preview_frame = np.full((64, 64), 73, dtype=np.uint8)
    replacement._preview_sequence = controller._preview_sequence
    tab.update_preview(replacement)
    assert tab.preview_label.pixmap().toImage().pixelColor(5, 5).red() == 73


def test_preview_timer_follows_frozen_config_and_stops_when_page_hidden(workspace):
    app, window, root = workspace
    controller = CameraController(CameraConfig(preview_fps=20, fps=120))
    window.services.controllers["camera"] = controller
    tab = window.widgets["camera"]
    tab.preview_fps_spin.setValue(1)
    window.pages.set_current_key("camera")
    settle(app)
    assert tab._preview_timer.isActive() and tab._preview_timer.interval() == 50
    assert controller.config.fps == 120
    window.pages.set_current_key("animals")
    settle(app)
    assert not tab._preview_timer.isActive()
    controller.config.preview_enabled = False
    window.pages.set_current_key("camera")
    settle(app)
    assert not tab._preview_timer.isActive()

