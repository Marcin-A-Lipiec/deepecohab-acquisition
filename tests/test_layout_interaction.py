"""Real mouse gestures must pan the view without editing the physical layout."""
import copy

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tests.test_navigation import workspace, settle


def drag(view, start, delta, button=Qt.LeftButton):
    viewport = view.viewport()
    QTest.mousePress(viewport, button, pos=start)
    for fraction in (0.25, 0.5, 0.75, 1):
        position = start + QPoint(round(delta.x() * fraction), round(delta.y() * fraction))
        event = QMouseEvent(QEvent.MouseMove, QPointF(position),
            QPointF(viewport.mapToGlobal(position)), Qt.NoButton, button, Qt.NoModifier)
        QApplication.sendEvent(viewport, event)
    QTest.mouseRelease(viewport, button, pos=start + delta)


def blank_position(view):
    for y in range(20, view.viewport().height() - 20, 30):
        for x in range(20, view.viewport().width() - 20, 30):
            position = QPoint(x, y)
            if view.hit(position) is None:
                return position
    raise AssertionError("No empty background for panning")


@pytest.mark.parametrize("theme,size", [("light", (1280, 800)), ("dark", (800, 600))])
def test_background_drag_after_fit_moves_view_and_preserves_graph(workspace, theme, size):
    app, window, root = workspace
    window.theme.set_mode(theme, persist=False)
    window.resize(*size)
    window.pages.set_current_key("layout")
    settle(app)
    layout = window.widgets["layout"]
    layout.fit()
    settle(app)
    original = copy.deepcopy(layout.read())
    cage = next(item for key, item in layout._items.items() if key.startswith("cage:"))
    before = layout.view.mapFromScene(cage.pos())
    delta = QPoint(75, 45)
    drag(layout.view, blank_position(layout.view), delta)
    settle(app)
    after = layout.view.mapFromScene(cage.pos())
    assert abs((after - before).x() - delta.x()) <= 3
    assert abs((after - before).y() - delta.y()) <= 3
    assert layout.read() == original
    assert not layout.view.fitted
    # Consecutive drags can move beyond the old fitted scrollbar range.
    for _ in range(3):
        drag(layout.view, blank_position(layout.view), delta)
    settle(app)
    transform = layout.view.transform()
    center = layout.view.mapToScene(layout.view.viewport().rect().center())
    layout.render_png(root / "panned-layout.png", longest_edge=640)
    assert layout.view.transform() == transform
    assert (layout.view.mapToScene(layout.view.viewport().rect().center()) - center).manhattanLength() < 3
    window.pages.set_current_key("events")
    settle(app)
    window.pages.set_current_key("layout")
    settle(app)
    assert layout.view.transform() == transform
    assert (layout.view.mapToScene(layout.view.viewport().rect().center()) - center).manhattanLength() < 3
    layout.fit()
    assert layout.view.fitted
    assert layout.read() == original


def test_middle_drag_over_cage_preserves_pending_tunnel_and_selection(workspace):
    app, window, root = workspace
    window.pages.set_current_key("layout")
    settle(app)
    layout = window.widgets["layout"]
    cage = next(item for key, item in layout._items.items() if key.startswith("cage:"))
    cage.setSelected(True)
    layout.start_tunnel(cage.cage)
    original = copy.deepcopy(layout.read())
    first = layout.first
    before = layout.view.mapFromScene(cage.pos())
    drag(layout.view, before, QPoint(-60, 40), Qt.MiddleButton)
    settle(app)
    after = layout.view.mapFromScene(cage.pos())
    assert abs((after - before).x() + 60) <= 3
    assert abs((after - before).y() - 40) <= 3
    assert layout.mode == "connect" and layout.first == first
    assert cage.isSelected() and layout.read() == original


def test_cage_drag_still_moves_cage_and_snaps_to_grid(workspace):
    app, window, root = workspace
    window.pages.set_current_key("layout")
    settle(app)
    layout = window.widgets["layout"]
    cage = next(item for key, item in layout._items.items() if key.startswith("cage:"))
    before = QPointF(cage.pos())
    target = before + QPointF(0, -layout.grid_step)
    start = layout.view.mapFromScene(before)
    delta = layout.view.mapFromScene(target) - start
    drag(layout.view, start, delta)
    settle(app)
    assert cage.pos() == target
    assert layout.graph["editor"]["cage_positions"][cage.cage["cell_id"]] == [target.x(), target.y()]
