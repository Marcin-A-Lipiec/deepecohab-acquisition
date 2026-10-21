"""Physical topology editor with stable IDs, branch attachments and readable labels."""

from __future__ import annotations
import copy
import math
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QActionGroup,
    QImage,
    QBrush,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QDialog,
    QDialogButtonBox,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QGraphicsLineItem,
    QMessageBox,
    QPushButton,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from deepecohab_recorder.ui.wheel_scroll import (
    QComboBox,
    QSpinBox,
)
from deepecohab_recorder.ui.theme import color, font, manager
from deepecohab_recorder.topology import (
    normalize_layout,
    preset_layout,
    validate_layout,
    validate_structure,
)
from deepecohab_recorder.ui.common import (
    config_buttons,
    help_button,
    wrapping_form,
)


def point(values):
    return QPointF(float(values[0]), float(values[1]))


def xy(value):
    return [round(value.x(), 2), round(value.y(), 2)]


def label_normal(a, b):
    """Use the same label side for a tunnel regardless of endpoint order."""
    delta = b - a
    length = math.hypot(delta.x(), delta.y())
    if not length:
        return QPointF(0, -1)
    normal = QPointF(-delta.y() / length, delta.x() / length)
    if ((abs(delta.x()) >= abs(delta.y()) and normal.y() > 0)
            or (abs(delta.x()) < abs(delta.y()) and normal.x() < 0)):
        normal = -normal
    return normal


def nearby_label_offsets(bounds, normal, clearance, scale):
    """Try the closest size-aware positions beside the tunnel or marker first."""
    half = QPointF(bounds.width() / 2, bounds.height() / 2)
    support = abs(normal.x()) * half.x() + abs(normal.y()) * half.y()
    tangent = QPointF(-normal.y(), normal.x())
    offsets = [normal * (side * (support + clearance + extra)) + tangent * slide - half
               for extra in (0, 8, 18, 32, 52, 80, 120)
               for slide in (0, 16, -16, 32, -32, 56, -56, 88, -88)
               for side in (1, -1)]
    offsets.sort(key=lambda offset: math.hypot((offset + half).x(), (offset + half).y()))
    return [offset / scale for offset in offsets]


class CageItem(QGraphicsRectItem):
    def __init__(self, editor, cage, position):
        compact = editor.view.viewport().height() < 380
        width = height = min(
            144, editor.grid_step * editor.view.transform().m11() * 0.7
        )
        super().__init__(-width / 2, -height / 2, width, height)
        self.editor, self.cage = editor, cage
        self.setPos(position)
        self.setZValue(3)
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
            | QGraphicsItem.ItemIgnoresTransformations
        )
        self.setBrush(color("accent-soft"))
        self.setPen(QPen(color("accent"), 2))
        title = self.title = QGraphicsSimpleTextItem(cage["name"], self)
        title.setAcceptedMouseButtons(Qt.NoButton)
        title.setFont(font(12 if compact else 14, bold=True))
        title.setBrush(color("ink"))
        title.setPos(-title.boundingRect().width() / 2, -height / 2 + 9)
        subtitle = self.subtitle = QGraphicsSimpleTextItem(
            cage.get("cage_type", "standard"), self
        )
        subtitle.setAcceptedMouseButtons(Qt.NoButton)
        subtitle.setFont(font(9 if compact else 10))
        subtitle.setBrush(color("ink-2"))
        if subtitle.boundingRect().width() > width - 12:
            subtitle.setScale((width - 12) / subtitle.boundingRect().width())
        subtitle.setPos(-min(width - 12, subtitle.boundingRect().width()) / 2, 6)
        self.setToolTip("Drag to move. Double-click to edit cage number/type.")

    def refresh_geometry(self):
        side = min(
            144, self.editor.grid_step * self.editor.view.transform().m11() * 0.7
        )
        self.setRect(-side / 2, -side / 2, side, side)
        self.title.setFont(font(14 if side >= 100 else 10, bold=True))
        self.title.setScale(
            min(1, (side - 10) / max(1, self.title.boundingRect().width()))
        )
        self.title.setPos(
            -self.title.boundingRect().width() * self.title.scale() / 2, -side / 2 + 10
        )
        self.subtitle.setVisible(side >= 65)
        self.subtitle.setScale(
            min(1, (side - 12) / max(1, self.subtitle.boundingRect().width()))
        )
        self.subtitle.setPos(
            -self.subtitle.boundingRect().width() * self.subtitle.scale() / 2, 6
        )

    def mousePressEvent(self, event):
        self._drag_start = QPointF(self.pos())
        self.editor.view.fitted = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        self.editor.graph["editor"]["cage_positions"][self.cage["cell_id"]] = xy(
            self.pos()
        )
        self.editor.defer_draw()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self.pos() != getattr(self, "_drag_start", self.pos()):
            target = self.editor.snap(self.pos())
            if self.editor.occupied(target, self.cage["cell_id"]):
                target = self._drag_start
                self.editor.hint.setText(
                    "That grid cell already contains a cage. Move cancelled."
                )
            self.setPos(target)
            self.editor.graph["editor"]["cage_positions"][self.cage["cell_id"]] = xy(
                target
            )
        self.editor.defer_draw()

    def mouseDoubleClickEvent(self, event):
        if self.editor.mode == "select":
            self.editor.edit_cage(self.cage)
        event.accept()


class TunnelItem(QGraphicsPathItem):
    def __init__(self, editor, tunnel, a, b):
        path = QPainterPath(a)
        path.lineTo(b)
        super().__init__(path)
        self.editor, self.tunnel = editor, tunnel
        self.setPen(QPen(color("ink-3"), 7, Qt.SolidLine, Qt.RoundCap))
        self.setFlag(QGraphicsItem.ItemIsSelectable)
        self.setZValue(0)
        self.setToolTip(
            tunnel["name"]
            + (" • dead end" if tunnel.get("dead_end") else "")
            + "\nDouble-click to edit endpoints, antenna count and antenna distances (cm)."
        )

    def boundingRect(self):
        return self.path().boundingRect().adjusted(-13, -13, 13, 13)

    def shape(self):
        stroke = QPainterPathStroker()
        stroke.setWidth(24)
        return stroke.createStroke(self.path())

    def mouseDoubleClickEvent(self, event):
        self.editor.edit_tunnel(self.tunnel)
        event.accept()


class AntennaItem(QGraphicsEllipseItem):
    def __init__(self, editor, tunnel, index, position):
        super().__init__(-11, -11, 22, 22)
        self.editor, self.tunnel, self.index = editor, tunnel, index
        self.setPos(position)
        self.setZValue(6)
        self.setBrush(color("warn"))
        self.setPen(QPen(color("warn-soft"), 2))
        self.setFlags(
            QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIgnoresTransformations
        )
        self.setToolTip(
            "Double-click to assign antenna / board number, or select and use Assign antenna."
        )

    def boundingRect(self):
        return self.rect().adjusted(-6, -6, 6, 6)

    def shape(self):
        path = QPainterPath()
        path.addEllipse(QRectF(-16, -16, 32, 32))
        return path

    def mouseDoubleClickEvent(self, event):
        self.editor.assign_antenna(self.tunnel, self.index)
        event.accept()


class DeviceAntennaItem(QGraphicsEllipseItem):
    def __init__(self, editor, device, position):
        super().__init__(-11, -11, 22, 22)
        self.editor, self.device = editor, device
        self.setPos(position)
        self.setZValue(7)
        self.setBrush(color("device"))
        self.setPen(QPen(color("surface"), 2))
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIgnoresTransformations)
        self.setToolTip('Device antenna. Double-click to edit role, name, position and TTL association; drag to reposition its marker.')

    def mouseDoubleClickEvent(self, event):
        self.editor.edit_device(self.device)
        event.accept()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.device['_editor_position'] = xy(self.pos())
        self.editor.defer_draw()


class LabelItem(QGraphicsRectItem):
    def __init__(self, editor, key, text, anchor, offset, role="ink"):
        super().__init__()
        self.editor, self.key, self.anchor = editor, key, anchor
        self.color_role = role
        label = self.label = QGraphicsSimpleTextItem(text, self)
        label.setAcceptedMouseButtons(Qt.NoButton)
        label.setFont(font(11, bold=True))
        label.setBrush(color(role))
        label.setPos(5, 3)
        bounds = label.boundingRect().adjusted(0, 0, 10, 6)
        self.setRect(bounds)
        self.setBrush(color("surface"))
        self.setPen(QPen(color("line"), 1))
        self.setPos(anchor + offset)
        self.setZValue(8)
        self.setFlags(
            QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIgnoresTransformations
        )
        self.setToolTip(
            "Drag this label to improve readability. Position is saved with the layout."
        )

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.editor.graph["editor"]["label_offsets"][self.key] = xy(
            self.pos() - self.anchor
        )
        self.editor.defer_draw()

    def mouseDoubleClickEvent(self, event):
        if self.editor.mode == "select":
            name = self.key.split(":")[0]
            tunnel = next(
                (t for t in self.editor.graph["tunnels"] if t["name"] == name), None
            )
            if tunnel:
                if ":antenna:" in self.key:
                    self.editor.assign_antenna(tunnel, int(self.key.rsplit(":", 1)[1]))
                else:
                    self.editor.edit_tunnel(tunnel)
        event.accept()


class EndItem(QGraphicsEllipseItem):
    def __init__(self, editor, name, position):
        super().__init__(-8, -8, 16, 16)
        self.editor, self.name = editor, name
        self.setPos(position)
        self.setZValue(4)
        self.setBrush(color("bad"))
        self.setPen(QPen(color("bad")))
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable)
        self.setToolTip("Dead-end tip: drag to change branch direction or length.")

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.editor.graph["editor"].setdefault("dead_end_positions", {})[self.name] = (
            xy(self.editor.snap(self.pos()))
        )
        self.editor.defer_draw()


class JunctionItem(QGraphicsEllipseItem):
    def __init__(self, junction_id):
        super().__init__(-9, -9, 18, 18)
        self.junction_id = junction_id
        self.setZValue(5)
        self.setPen(QPen(color("accent-soft")))
        self.setBrush(color("accent"))
        self.setToolTip("Junction: use Add tunnel to connect here.")


class LayoutView(QGraphicsView):
    def __init__(self, editor, scene):
        super().__init__(scene)
        self.editor = editor
        self.fitted = True
        self.show_grid = True
        self._fit_pending = False
        self._pan_position = None
        self._pan_button = Qt.NoButton
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setMinimumSize(320, 120)
        self.setBackgroundBrush(color("ground"))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "fitted", False) and not self._fit_pending:
            self._fit_pending = True
            QTimer.singleShot(0, self._fit_after_resize)

    def viewportEvent(self, event):
        result = super().viewportEvent(event)
        if event.type() == QEvent.Resize:
            QTimer.singleShot(0, self.place_help)
        return result

    def place_help(self):
        button = getattr(getattr(self, "editor", None), "layout_help", None)
        if button is not None:
            button.move(
                max(0, self.viewport().width() - button.width() - 8),
                max(0, self.viewport().height() - button.height() - 8),
            )
            button.raise_()

    def _fit_after_resize(self):
        self._fit_pending = False
        if self.fitted:
            self.editor.fit()

    def content_bounds(self):
        """Visible bounds, including items whose fonts ignore scene scaling."""
        bounds = QRectF()
        transform = self.viewportTransform()
        for item in self.scene().items():
            if item.isVisible():
                bounds = bounds.united(
                    item.deviceTransform(transform).mapRect(item.boundingRect())
                )
        return bounds

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        scale = self.transform().m11() * factor
        if 0.2 <= scale <= 3:
            self.fitted = False
            self.scale(factor, factor)
            self.editor.defer_draw()
        event.accept()

    def drawBackground(self, painter, rect):
        painter.fillRect(rect, color("ground"))
        if not self.show_grid:
            return
        step = self.editor.grid_step
        pen = QPen(color("line"), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        left = math.floor(rect.left() / step) * step
        top = math.floor(rect.top() / step) * step
        for x in range(int(left), int(rect.right()) + step, step):
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        for y in range(int(top), int(rect.bottom()) + step, step):
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

    def hit(self, position, *, connection=False):
        seen = set()
        for item in self.items(position):
            while item.parentItem():
                item = item.parentItem()
            if id(item) in seen:
                continue
            seen.add(id(item))
            if connection and isinstance(item, LabelItem):
                name = item.key.split(":")[0]
                item = self.editor._items.get("tunnel:" + name)
            if isinstance(
                item,
                (CageItem, TunnelItem, DeviceAntennaItem, AntennaItem, LabelItem, EndItem, JunctionItem),
            ):
                return item
        return None

    def connection_hit(self, position):
        item = self.hit(position, connection=True)
        if not isinstance(item, TunnelItem):
            return item
        tunnels = {}
        for candidate in self.items(position):
            while candidate.parentItem():
                candidate = candidate.parentItem()
            if isinstance(candidate, TunnelItem):
                tunnels[candidate.tunnel["name"]] = candidate
        if len(tunnels) <= 1:
            return item
        menu = QMenu(self)
        menu.addSection("Choose the tunnel at this crossing")
        actions = {menu.addAction(name): value for name, value in tunnels.items()}
        chosen = menu.exec(self.viewport().mapToGlobal(position))
        return actions.get(chosen, False)

    def ensure_pan_space(self):
        """Give a fitted scene room to move, without changing model coordinates."""
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        anchor = visible.center()
        before = self.mapFromScene(anchor)
        self.setSceneRect(self.scene().sceneRect().united(visible).adjusted(
            -visible.width(), -visible.height(), visible.width(), visible.height()))
        shift = self.mapFromScene(anchor) - before
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + shift.x())
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() + shift.y())

    def start_pan(self, event):
        self.fitted = False
        self.ensure_pan_space()
        self._pan_position = event.position().toPoint()
        self._pan_button = event.button()
        self._pan_cursor = self.viewport().cursor()
        self.viewport().setCursor(Qt.ClosedHandCursor)
        event.accept()

    def mousePressEvent(self, event):
        # Middle-drag also works over items and while a connection tool is active.
        if event.button() == Qt.MiddleButton:
            self.start_pan(event)
            return
        if event.button() == Qt.RightButton and self.editor.mode != "select":
            self.editor.set_mode("select")
            event.accept()
            return
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        position = self.mapToScene(event.position().toPoint())
        mode = self.editor.mode
        item = (
            self.connection_hit(event.position().toPoint())
            if mode != "select"
            else self.hit(event.position().toPoint())
        )
        if item is False:
            event.accept()
            return
        if mode == "cage":
            if item is None:
                self.editor.add_cage(position)
            else:
                self.editor.hint.setText("Choose an empty grid cell.")
        elif mode in ("connect", "branch", "dead"):
            if self.editor.first is None and self.editor.branch_start is None:
                if isinstance(item, CageItem):
                    self.editor.start_tunnel(item.cage)
                elif isinstance(item, JunctionItem):
                    self.editor.first = item.junction_id
                elif isinstance(item, TunnelItem):
                    self.editor.start_branch(item.tunnel, position)
                else:
                    self.editor.hint.setText(
                        "Choose a cage, junction or clear tunnel segment to start."
                    )
            else:
                self.editor.finish_connection(item, position)
        else:
            self.fitted = False
            if item is None:
                self.scene().clearSelection()
                self.start_pan(event)
                return
            super().mousePressEvent(event)
            return
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if self.editor.mode != "select" or event.button() != Qt.LeftButton:
            event.accept()
            return
        item = self.hit(event.position().toPoint())
        if isinstance(item, CageItem):
            self.editor.edit_cage(item.cage)
        elif isinstance(item, DeviceAntennaItem):
            self.editor.edit_device(item.device)
        elif isinstance(item, AntennaItem):
            self.editor.assign_antenna(item.tunnel, item.index)
        elif isinstance(item, TunnelItem):
            self.editor.edit_tunnel(item.tunnel)
        elif isinstance(item, LabelItem):
            if item.key.startswith('device:'):
                device = next((d for d in self.editor.devices if d['antenna'] == item.key.split(':', 1)[1]), None)
                if device: self.editor.edit_device(device)
                event.accept()
                return
            name = item.key.split(":")[0]
            tunnel = next(
                (t for t in self.editor.graph["tunnels"] if t["name"] == name), None
            )
            if tunnel:
                if ":antenna:" in item.key:
                    self.editor.assign_antenna(tunnel, int(item.key.rsplit(":", 1)[1]))
                else:
                    self.editor.edit_tunnel(tunnel)
        event.accept()

    def mouseMoveEvent(self, event):
        if self._pan_position is not None:
            delta = event.position().toPoint() - self._pan_position
            # Extend before approaching an edge so repeated drags cannot get stuck.
            self.ensure_pan_space()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self._pan_position = event.position().toPoint()
            event.accept()
            return
        if self.editor.first or self.editor.mode == "branch":
            a = (
                self.editor.endpoint(self.editor.first)
                if self.editor.first
                else self.editor.branch_start[1]
            )
            b = self.mapToScene(event.position().toPoint())
            self.editor.draft.setLine(a.x(), a.y(), b.x(), b.y())
            self.editor.draft.show()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._pan_position is not None and event.button() == self._pan_button:
            self._pan_position = None
            self._pan_button = Qt.NoButton
            self.viewport().setCursor(self._pan_cursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.editor.set_mode("select")
            event.accept()
        elif event.key() == Qt.Key_Delete and self.editor.mode == "select":
            self.editor.delete_selected()
            event.accept()
        else:
            super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        self.editor.set_mode("select")
        item = self.hit(event.pos())
        position = self.mapToScene(event.pos())
        menu = QMenu(self)
        menu.setToolTipsVisible(True)

        def action(title, hint, callback):
            value = menu.addAction(title)
            value.setToolTip(hint)
            value.triggered.connect(callback)

        if isinstance(item, CageItem):
            action(
                "Cage properties…",
                "Edit this cage's number and type.",
                lambda: self.editor.edit_cage(item.cage),
            )
            action(
                "Start tunnel here",
                "Then click a destination cage or an empty grid cell.",
                lambda: self.editor.start_tunnel(item.cage),
            )
        elif isinstance(item, DeviceAntennaItem):
            action('Edit device antenna…', 'Set role, name, position and TTL association.', lambda: self.editor.edit_device(item.device))
        elif isinstance(item, (TunnelItem, AntennaItem)):
            if isinstance(item, AntennaItem):
                action(
                    "Assign antenna…",
                    "Choose its physical channel and DAQ board number, or match a tag.",
                    lambda: self.editor.assign_antenna(item.tunnel, item.index),
                )
            action(
                "Tunnel properties…",
                "Change endpoints or the number of physical antennas.",
                lambda: self.editor.edit_tunnel(item.tunnel),
            )
            action(
                "Start branch here",
                "Then click a cage, another tunnel, or an empty grid cell for a one-antenna dead end.",
                lambda: self.editor.start_branch(item.tunnel, position),
            )
        elif item is None:
            action(
                "Add cage here",
                "Place a cage in this empty grid cell.",
                lambda: self.editor.add_cage(position),
            )
        if isinstance(item, (CageItem, TunnelItem, DeviceAntennaItem)):
            menu.addSeparator()

            def remove():
                self.scene().clearSelection()
                item.setSelected(True)
                self.editor.delete_selected()

            action(
                "Delete…",
                "Review and confirm deletion of this item and its dependent connections.",
                remove,
            )
        if not menu.isEmpty():
            menu.exec(event.globalPos())


class EcoHABLayoutTab(QWidget):
    paths = ("recording.layout", "recording.devices")

    def __init__(self, ui):
        super().__init__()
        self.ui = ui
        self.graph = preset_layout()
        self.devices = []
        self.mode = "select"
        self.first = None
        self._pending_draw = False
        self._items = {}
        self._drawing = False
        self.branch_start = None
        root = QVBoxLayout(self)
        root.addLayout(config_buttons(self, "the layout"))
        self.toolbar = QToolBar("Tools")
        self.toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        self.tools = {}
        for label, mode, hint in [
            (
                "Select / move",
                "select",
                "Click to select. Drag a cage to another grid cell. Double-click a cage, antenna or tunnel to edit it.",
            ),
            (
                "Add cage",
                "cage",
                "Click an empty grid cell to add one cage. Occupied cells are protected. Returns to Select / move afterward.",
            ),
            (
                "Add tunnel",
                "connect",
                "Click a starting cage, then another cage. An empty destination cell makes a one-antenna dead end. Escape cancels.",
            ),
        ]:
            action = self.toolbar.addAction(label)
            action.setCheckable(True)
            action.setToolTip(hint)
            self.tool_group.addAction(action)
            self.tools[mode] = action
            action.triggered.connect(lambda checked=False, m=mode: self.set_mode(m))
        add_device = self.toolbar.addAction('Add device antenna…')
        add_device.triggered.connect(lambda: self.edit_device())
        root.addWidget(self.toolbar)
        edit = QToolBar("View and placement")
        for label, callback, hint in [
            (
                "Fit layout",
                self.fit,
                "Fit the current layout in the view. Mouse wheel zooms; drag empty background to pan.",
            ),
            (
                "Align cages to grid",
                self.align_grid,
                "Move existing cages onto separate grid cells, preserving their identities and connections.",
            ),
            (
                "Four-cage preset",
                self.preset,
                "Replace the layout with a four-cage arrangement after confirmation.",
            ),
        ]:
            action = edit.addAction(label)
            action.setToolTip(hint)
            action.triggered.connect(callback)
        edit.addSeparator()
        export = edit.addAction("Export PNG…")
        export.setToolTip(
            "Save the complete layout as a PNG image, including offscreen labels."
        )
        export.triggered.connect(self.export_png)
        root.addWidget(edit)
        self.hint = QLabel(
            "Select / move: drag cages to grid cells. Double-click to edit. Right-click for connections and deletion."
        )
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)
        self.scene = QGraphicsScene(self)
        self.scene.setItemIndexMethod(QGraphicsScene.NoIndex)
        self.view = LayoutView(self, self.scene)
        self.draft = QGraphicsLineItem()
        self.draft.setPen(QPen(color("accent"), 2, Qt.DashLine))
        self.draft.setAcceptedMouseButtons(Qt.NoButton)
        self.draft.setZValue(20)
        self.scene.addItem(self.draft)
        self.draft.hide()
        root.addWidget(self.view, 1)
        self.summary = QLabel()
        edit.addSeparator()
        edit.addWidget(self.summary)
        self.layout_help = help_button(
            self,
            "Antenna identifiers and physical connections",
            "An antenna identifier is its physical channel followed by the DAQ board number: 3-1 or E-2. "
            "Board v1.0 uses 0–9/A–F, v1.5 uses A–P, and the old board uses 1–8. TTL inputs are separate.\n\n"
            "Assign antenna order from a tunnel’s start endpoint to its end endpoint. Tunnel numbers are automatic and stable. "
            "Double-click a tunnel to enter centre-to-centre distances between successive antennas in centimetres; unknown distances stay unset. Save the layout to write these measurements into the main config.\n\n"
            "Right-click a tunnel and choose Start branch here; click an empty cell to place its one-antenna dead end. "
            "Drag the red tip to place it. Parent tunnel identity is retained.\n\n"
            "Only direct, physical cage/tunnel antenna relations are exported. Special dead-end behavior is reserved for the later analysis update. "
            "Labels can be dragged independently; reset label positions to restore automatic placement.",
        )
        self.layout_help.setParent(self.view.viewport())
        self.layout_help.setToolTip(
            "Explain antenna identifiers and physical connections."
        )
        self.view.place_help()
        self.set_mode("select")
        self.draw()
        manager().changed.connect(self.apply_theme)

    def apply_theme(self, *args):
        """Recolor existing scene items without touching layout or view state."""
        self.view.setBackgroundBrush(color("ground"))
        self.draft.setPen(QPen(color("accent"), 2, Qt.DashLine))
        for item in self._items.values():
            if isinstance(item, CageItem):
                item.setBrush(color("accent-soft"))
                item.setPen(QPen(color("accent"), 2))
                item.title.setBrush(color("ink"))
                item.subtitle.setBrush(color("ink-2"))
            elif isinstance(item, TunnelItem):
                item.setPen(QPen(color("ink-3"), 7, Qt.SolidLine, Qt.RoundCap))
            elif isinstance(item, AntennaItem):
                item.setBrush(color("warn"))
                item.setPen(QPen(color("warn-soft"), 2))
            elif isinstance(item, DeviceAntennaItem):
                item.setBrush(color("device"))
                item.setPen(QPen(color("surface"), 2))
            elif isinstance(item, LabelItem):
                item.setBrush(color("surface"))
                item.setPen(QPen(color("line"), 1))
                item.label.setBrush(color(item.color_role))
            elif isinstance(item, EndItem):
                item.setBrush(color("bad"))
                item.setPen(QPen(color("bad")))
            elif isinstance(item, JunctionItem):
                item.setBrush(color("accent"))
                item.setPen(QPen(color("accent-soft")))
            elif isinstance(item, QGraphicsLineItem):
                item.setPen(QPen(color("line-strong"), 1))
        self.view.viewport().update()

    @property
    def grid_step(self):
        return 240

    def snap(self, position):
        step = self.grid_step
        return QPointF(
            (math.floor(position.x() / step) + 0.5) * step,
            (math.floor(position.y() / step) + 0.5) * step,
        )

    def occupied(self, position, exclude=None):
        target = self.snap(position)
        return any(
            c["cell_id"] != exclude and self.snap(self.endpoint(c["cell_id"])) == target
            for c in self.graph["cages"]
        )

    def align_grid(self, confirm=True):
        if (
            confirm
            and self.graph["cages"]
            and QMessageBox.question(
                self,
                "Align cages",
                "Align cage positions to separate grid cells? Antenna assignments and connections are retained.",
            )
            != QMessageBox.Yes
        ):
            return
        used = set()
        for cage in self.graph["cages"]:
            base = self.snap(self.endpoint(cage["cell_id"]))
            chosen = base
            for radius in range(len(self.graph["cages"]) + 1):
                candidates = [
                    (dx, dy)
                    for dx in range(-radius, radius + 1)
                    for dy in range(-radius, radius + 1)
                    if max(abs(dx), abs(dy)) == radius
                ]
                available = [
                    base + QPointF(dx * self.grid_step, dy * self.grid_step)
                    for dx, dy in candidates
                    if (base.x() + dx * self.grid_step, base.y() + dy * self.grid_step)
                    not in used
                ]
                if available:
                    chosen = available[0]
                    break
            used.add((chosen.x(), chosen.y()))
            self.graph["editor"]["cage_positions"][cage["cell_id"]] = xy(chosen)
        self.graph["editor"]["label_offsets"] = {}
        self.draw()
        self.fit()

    def set_mode(self, mode):
        self.mode, self.first = mode, None
        self.branch_start = None
        self.tools["connect" if mode in ("dead", "branch") else mode].setChecked(True)
        if hasattr(self, "draft"):
            self.draft.hide()
            self.draft.setLine(0, 0, 0, 0)
        messages = {
            "select": "Select / move: drag cages to grid cells. Drag empty space to pan; middle-drag pans in any tool. Double-click to edit. Right-click for connections and deletion.",
            "cage": "Add cage: click an empty grid cell. Escape cancels.",
            "connect": "Add tunnel: click the starting cage, then another cage or an empty cell for a dead end. Escape cancels.",
            "dead": "Add tunnel: click a cage, then an empty grid cell. Escape cancels.",
            "branch": "Branch: click a cage, another tunnel, or an empty grid cell for a one-antenna dead end. Escape cancels.",
        }
        self.hint.setText(messages[mode])
        self.view.setCursor(Qt.ArrowCursor if mode == "select" else Qt.CrossCursor)

    def start_tunnel(self, cage):
        self.set_mode("connect")
        self.first = cage["cell_id"]
        self.hint.setText(
            f"Tunnel from {cage['name']}: click another cage, or an empty cell for a dead end. Escape cancels."
        )

    def start_branch(self, parent, position):
        self.set_mode("branch")
        self.branch_start = (parent, position)

    def keep(self, key, factory):
        self._seen.add(key)
        item = self._items.get(key)
        if item is None:
            item = factory()
            self._items[key] = item  # Explicit ownership survives PySide hit-testing.
            self.scene.addItem(item)
        return item

    def endpoint(self, endpoint, seen=None):
        positions = self.graph["editor"]["cage_positions"]
        if endpoint in positions:
            return point(positions[endpoint])
        seen = set(seen or ())
        if endpoint in seen:
            return QPointF()
        seen.add(endpoint)
        junction = next(
            (j for j in self.graph["junctions"] if j["id"] == endpoint), None
        )
        if junction:
            parent = next(
                (
                    t
                    for t in self.graph["tunnels"]
                    if t["name"] == junction["parent_tunnel"]
                ),
                None,
            )
            if parent:
                a, b = self.ends(parent, seen)
                return a + (b - a) * junction.get("position", 0.5)
        return QPointF()

    def ends(self, tunnel, seen=None):
        a = self.endpoint(tunnel["start_cell_id"], seen)
        if tunnel.get("dead_end"):
            b = point(
                self.graph["editor"]
                .get("dead_end_positions", {})
                .get(tunnel["name"], xy(a + QPointF(140, 100)))
            )
        else:
            b = self.endpoint(tunnel.get("end_cell_id"), seen)
        return a, b

    def defer_draw(self):
        if not self._pending_draw:
            from PySide6.QtCore import QTimer

            self._pending_draw = True
            QTimer.singleShot(0, self.draw)

    def draw(self):
        self._pending_draw = False
        self._seen = set()
        occupied, labels, tunnel_paths = [], [], []
        scale = max(0.01, self.view.transform().m11())
        for cage in self.graph["cages"]:
            position = self.endpoint(cage["cell_id"])
            cage_item = self.keep(
                "cage:" + cage["cell_id"], lambda: CageItem(self, cage, position)
            )
            cage_item.cage = cage
            cage_item.title.setText(cage["name"])
            cage_item.title.setPos(
                -cage_item.title.boundingRect().width() / 2, cage_item.title.pos().y()
            )
            cage_item.subtitle.setText(cage.get("cage_type", "standard"))
            cage_item.subtitle.setPos(
                -min(
                    cage_item.rect().width() - 12,
                    cage_item.subtitle.boundingRect().width(),
                )
                / 2,
                6,
            )
            cage_item.refresh_geometry()
            cage_item.setPos(position)
            cage_rect = cage_item.rect()
            occupied.append(
                QRectF(
                    position.x() + (cage_rect.left() - 2) / scale,
                    position.y() + (cage_rect.top() - 2) / scale,
                    (cage_rect.width() + 4) / scale,
                    (cage_rect.height() + 4) / scale,
                )
            )
        for tunnel in self.graph["tunnels"]:
            a, b = self.ends(tunnel)
            tunnel_item = self.keep(
                "tunnel:" + tunnel["name"], lambda: TunnelItem(self, tunnel, a, b)
            )
            tunnel_item.tunnel = tunnel
            path = QPainterPath(a)
            path.lineTo(b)
            tunnel_item.setPath(path)
            stroke = QPainterPathStroker()
            stroke.setWidth(tunnel_item.pen().widthF() + 2 / scale)
            tunnel_paths.append(stroke.createStroke(path))
            normal = label_normal(a, b)
            labels.append(
                (tunnel["name"], tunnel["name"], a + (b - a) * 0.5, "ink-2", normal)
            )
            if tunnel.get("dead_end"):
                end = self.keep(
                    "end:" + tunnel["name"], lambda: EndItem(self, tunnel["name"], b)
                )
                end.setPos(b)
                occupied.append(end.sceneBoundingRect().adjusted(-2 / scale, -2 / scale, 2 / scale, 2 / scale))
            antennas = tunnel.get("antennas", [])
            for i, source in enumerate(antennas):
                f = (i + 1) / (len(antennas) + 1)
                position = a + (b - a) * f
                antenna = self.keep(
                    f"antenna:{tunnel['name']}:{i}",
                    lambda: AntennaItem(self, tunnel, i, position),
                )
                antenna.tunnel = tunnel
                antenna.setPos(position)
                occupied.append(
                    QRectF(
                        position.x() - 12 / scale,
                        position.y() - 12 / scale,
                        24 / scale,
                        24 / scale,
                    )
                )
                labels.append(
                    (f"{tunnel['name']}:antenna:{i}", source, position, "warn", -normal)
                )
        for i, device in enumerate(self.devices):
            cage = next((c for c in self.graph['cages'] if c['name'] == device.get('position')), None)
            tunnel = next((t for t in self.graph['tunnels'] if t['name'] == device.get('position')), None)
            if device.get('_editor_position'):
                position = point(device['_editor_position'])
            elif cage:
                position = self.endpoint(cage['cell_id']) + QPointF(35 + (i % 3) * 35, 35)
            elif tunnel:
                a, b = self.ends(tunnel)
                position = (a + b) / 2 + QPointF(0, 40)
            else:
                position = QPointF(100 + i * 60, 100)
            key = 'device:' + device['antenna']
            marker = self.keep(key, lambda: DeviceAntennaItem(self, device, position))
            marker.device = device
            marker.setPos(position)
            occupied.append(QRectF(position.x()-12/scale, position.y()-12/scale, 24/scale, 24/scale))
            labels.append((key, device['antenna'] + ' / ' + device['name'], position, "device", QPointF(0, -1)))
        for junction in self.graph["junctions"]:
            p = self.endpoint(junction["id"])
            node = self.keep(
                "junction:" + junction["id"],
                lambda: JunctionItem(junction["id"]),
            )
            node.setPen(QPen(color("accent-soft")))
            node.setBrush(QBrush(color("accent")))
            node.setAcceptedMouseButtons(Qt.NoButton)
            node.setPos(p)
            node.setZValue(5)
            node.setToolTip(f"{junction['id']} on {junction['parent_tunnel']}")
            occupied.append(node.sceneBoundingRect().adjusted(-2 / scale, -2 / scale, 2 / scale, 2 / scale))
        for key, text, anchor, role, normal in labels:
            saved = self.graph["editor"]["label_offsets"].get(key)
            label = self.keep(
                "label:" + key,
                lambda: LabelItem(self, key, text, anchor, QPointF(), role),
            )
            label.anchor = anchor
            label.color_role = role
            label.label.setBrush(color(role))
            label.label.setText(text)
            label.setRect(label.label.boundingRect().adjusted(0, 0, 10, 6))
            clearance = 16 if role in ("warn", "device") else 3.5 * scale + 6
            offsets = ([point(saved)] if saved else
                       nearby_label_offsets(label.rect(), normal, clearance, scale))
            chosen = None
            for offset in offsets:
                rect = (
                    QRectF(
                        0,
                        0,
                        label.rect().width() / scale,
                        label.rect().height() / scale,
                    )
                    .translated(anchor + offset)
                    .adjusted(-3 / scale, -3 / scale, 3 / scale, 3 / scale)
                )
                if saved or (not any(rect.intersects(other) for other in occupied)
                        and not any(path.intersects(rect) for path in tunnel_paths)):
                    chosen = offset
                    break
            if chosen is None:
                # Only a very crowded layout needs a position outside its envelope.
                top = min([rect.top() for rect in occupied]
                          + [path.boundingRect().top() for path in tunnel_paths] + [anchor.y()])
                chosen = QPointF(-label.rect().width() / (2 * scale),
                                 top - anchor.y() - (label.rect().height() + 7) / scale)
            label.setPos(anchor + chosen)
            occupied.append(
                QRectF(
                    0, 0, label.rect().width() / scale, label.rect().height() / scale
                )
                .translated(label.pos())
                .adjusted(-3 / scale, -3 / scale, 3 / scale, 3 / scale)
            )
            leader = self.keep("leader:" + key, QGraphicsLineItem)
            label_rect = QRectF(label.pos(), label.rect().size() / scale)
            nearest = QPointF(max(label_rect.left(), min(anchor.x(), label_rect.right())),
                              max(label_rect.top(), min(anchor.y(), label_rect.bottom())))
            leader.setLine(anchor.x(), anchor.y(), nearest.x(), nearest.y())
            leader.setPen(QPen(color("line-strong"), 1))
            leader.setAcceptedMouseButtons(Qt.NoButton)
            leader.setZValue(-1)
        for key in set(self._items) - self._seen:
            item = self._items.pop(key)
            self.scene.removeItem(item)
        rect = self.graph_bounds()
        self.scene.setSceneRect(
            rect.adjusted(-160, -160, 160, 160)
            if not rect.isNull()
            else QRectF(0, 0, 900, 650)
        )
        self.summary.setText(
            f"{len(self.graph['cages'])} cages  •  {len(self.graph['tunnels'])} tunnels  •  {len(self.graph['junctions'])} junctions  •  {len(self.devices)} devices"
        )

    def graph_bounds(self):
        bounds = QRectF()
        for item in self._items.values():
            if item.isVisible():
                bounds = bounds.united(item.sceneBoundingRect())
        return bounds

    def fit(self):
        self.view.setSceneRect(QRectF())
        bounds = self.graph_bounds()
        if bounds.isEmpty():
            self.view.resetTransform()
            self.scene.setSceneRect(0, 0, 900, 650)
            self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)
            self.view.fitted = True
            return
        if self._items:
            self.view.fitted = True
            self.view.fitInView(
                bounds.adjusted(-45, -45, 45, 45),
                Qt.KeepAspectRatio,
            )
            # Qt's scene bounds do not account for fixed-size cage and label
            # rendering. Refine using actual viewport bounds after placement.
            for _ in range(8):
                self.draw()
                bounds = self.view.content_bounds()
                available = self.view.viewport().rect().adjusted(14, 14, -14, -14)
                if bounds.isEmpty() or available.isEmpty():
                    break
                factor = min(
                    available.width() / bounds.width(),
                    available.height() / bounds.height(),
                )
                self.view.centerOn(self.view.mapToScene(bounds.center().toPoint()))
                if factor >= 0.99:
                    break
                self.view.scale(max(factor * 0.97, 0.5), max(factor * 0.97, 0.5))
            self.draw()
            bounds = self.view.content_bounds()
            self.view.centerOn(self.view.mapToScene(bounds.center().toPoint()))

    def showEvent(self, event):
        super().showEvent(event)
        if self.view.fitted:
            QTimer.singleShot(0, self.fit)

    def reset_labels(self):
        self.graph["editor"]["label_offsets"] = {}
        self.draw()

    def next_tunnel(self):
        editor = self.graph["editor"]
        current = (
            max(
                [int(t.get("tunnel_no", 0)) for t in self.graph["tunnels"]]
                + [int(editor.get("next_tunnel_number", 1)) - 1]
            )
            + 1
        )
        editor["next_tunnel_number"] = current + 1
        return current

    def add_cage(self, position):
        position = self.snap(position)
        if self.occupied(position):
            self.hint.setText(
                "This grid cell already contains a cage. Choose an empty cell, or Escape to cancel."
            )
            return
        number = (
            max(
                [
                    int(c["name"].removeprefix("cage_"))
                    for c in self.graph["cages"]
                    if c["name"].removeprefix("cage_").isdigit()
                ]
                + [0]
            )
            + 1
        )
        cell = f"C{number}"
        while any(c["cell_id"] == cell for c in self.graph["cages"]):
            number += 1
            cell = f"C{number}"
        self.graph["cages"].append(
            {
                "name": f"cage_{number}",
                "cell_id": cell,
                "cage_type": "standard",
                "antennas": [],
            }
        )
        self.graph["editor"]["cage_positions"][cell] = xy(position)
        self.draw()
        self.set_mode("select")

    def add_tunnel(self, start, end, position=None):
        if start == end:
            self.hint.setText("Choose a different destination cage.")
            return
        if end and any(
            {t.get("start_cell_id"), t.get("end_cell_id")} == {start, end}
            for t in self.graph["tunnels"]
        ):
            self.hint.setText(
                "These endpoints are already connected. Edit the existing tunnel."
            )
            return
        number = self.next_tunnel()
        name = f"tunnel_{number}"
        tunnel = {
            "name": name,
            "tunnel_no": number,
            "start_cell_id": start,
            "end_cell_id": end,
            "dead_end": end is None,
            "antennas": ["?"] if end is None else ["?", "?"],
        }
        self.graph["tunnels"].append(tunnel)
        if position is not None:
            self.graph["editor"].setdefault("dead_end_positions", {})[name] = xy(
                position
            )
        self.draw()
        return tunnel

    def connection_endpoint(self, item, position):
        if isinstance(item, CageItem):
            return item.cage["cell_id"]
        if isinstance(item, JunctionItem):
            return item.junction_id
        if not isinstance(item, TunnelItem):
            raise ValueError(
                "Choose a cage, a junction, a clear tunnel segment, or empty space."
            )
        parent = item.tunnel
        a, b = self.ends(parent)
        vector = b - a
        length2 = vector.x() ** 2 + vector.y() ** 2
        if length2 < 1:
            raise ValueError("The parent tunnel is too short.")
        fraction = (
            (position.x() - a.x()) * vector.x() + (position.y() - a.y()) * vector.y()
        ) / length2
        if not 0.02 < fraction < 0.98:
            raise ValueError("Choose an interior portion of the tunnel.")
        for i in range(len(parent.get("antennas", []))):
            if (
                abs(fraction - (i + 1) / (len(parent["antennas"]) + 1))
                * math.sqrt(length2)
                * self.view.transform().m11()
                < 18
            ):
                raise ValueError(
                    "Place the junction between antennas, not on an antenna."
                )
        for j in self.graph["junctions"]:
            if (
                j["parent_tunnel"] == parent["name"]
                and abs(j["position"] - fraction) < 0.01
            ):
                return j["id"]
        number = 1
        used = {j["id"] for j in self.graph["junctions"]}
        while f"junction_{number}" in used:
            number += 1
        name = f"junction_{number}"
        self.graph["junctions"].append(
            {"id": name, "parent_tunnel": parent["name"], "position": fraction}
        )
        return name

    def finish_connection(self, item, position):
        before = copy.deepcopy(self.graph)
        try:
            if self.first:
                start = self.first
            else:
                parent, source_position = self.branch_start
                start = self.connection_endpoint(
                    self._items["tunnel:" + parent["name"]], source_position
                )
            end = self.connection_endpoint(item, position) if item is not None else None
            tip = self.snap(position) if end is None else None
            if tip is not None and (tip - self.endpoint(start)).manhattanLength() < 20:
                raise ValueError("Choose a different destination cell.")
            tunnel = self.add_tunnel(start, end, tip)
            if tunnel is None:
                raise ValueError(self.hint.text())
            problems = validate_structure(self.graph)
            if problems:
                raise ValueError(problems[0])
        except ValueError as exc:
            self.graph = before
            self.draw()
            self.hint.setText(str(exc) + " Escape cancels.")
            return
        self.set_mode("select")
        self.draw()

    def render_png(self, path, include_grid=False, longest_edge=2400):
        """Render an independent view so exporting cannot disturb editing state."""
        from PySide6.QtCore import QRect, QSaveFile, QIODevice

        clone = EcoHABLayoutTab(self.ui)
        try:
            clone.graph = copy.deepcopy(self.graph)
            clone.resize(1400, 1200)
            clone.layout().activate()
            clone.view.resize(1360, 1000)
            clone.view.viewport().resize(1358, 998)
            clone.view.show_grid = include_grid
            clone.layout_help.hide()
            clone.draw()
            clone.fit()
            # Freeze device-sized items into scene units so raster scaling also
            # scales text/markers, as it scales tunnel lines.
            scale = clone.view.transform().m11()
            for item in clone.scene.items():
                if item.flags() & QGraphicsItem.ItemIgnoresTransformations:
                    item.setFlag(QGraphicsItem.ItemIgnoresTransformations, False)
                    item.setScale(1 / scale)
            bounds = (
                clone.view.content_bounds().adjusted(-25, -25, 25, 25).toAlignedRect()
            )
            if bounds.isEmpty():
                bounds = QRect(0, 0, 900, 650)
            ratio = longest_edge / max(bounds.width(), bounds.height())
            image = QImage(
                max(1, round(bounds.width() * ratio)),
                max(1, round(bounds.height() * ratio)),
                QImage.Format_ARGB32,
            )
            image.fill(color("ground"))
            painter = QPainter(image)
            clone.view.render(painter, QRectF(image.rect()), bounds)
            painter.end()
            target = QSaveFile(str(path))
            if (
                not target.open(QIODevice.WriteOnly)
                or not image.save(target, "PNG")
                or not target.commit()
            ):
                raise OSError("Could not save PNG. Check the selected directory.")
        finally:
            clone.close()
            clone.deleteLater()

    def export_png(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Export layout PNG")
        form = wrapping_form(dialog)
        grid = QCheckBox("Include grid")
        form.addRow(grid)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        form.addRow(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if not dialog.exec():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save layout PNG", "EcoHAB_layout.png", "PNG image (*.png)"
        )
        if path:
            try:
                self.render_png(
                    path if path.lower().endswith(".png") else path + ".png",
                    grid.isChecked(),
                )
            except OSError as exc:
                QMessageBox.warning(self, "Export PNG", str(exc))

    def add_branch(self, parent, position=None, tip=None):
        a, b = self.ends(parent)
        vector = b - a
        norm = vector.x() ** 2 + vector.y() ** 2
        fraction = (
            0.5
            if position is None or norm == 0
            else (
                (position.x() - a.x()) * vector.x()
                + (position.y() - a.y()) * vector.y()
            )
            / norm
        )
        fraction = max(0.05, min(0.95, fraction))
        number = (
            max(
                [
                    int(j["id"].removeprefix("junction_"))
                    for j in self.graph["junctions"]
                    if j["id"].removeprefix("junction_").isdigit()
                ]
                + [0]
            )
            + 1
        )
        name = f"junction_{number}"
        self.graph["junctions"].append(
            {"id": name, "parent_tunnel": parent["name"], "position": fraction}
        )
        anchor = a + vector * fraction
        length = max(1, math.sqrt(norm))
        normal = QPointF(-vector.y() / length * 175, vector.x() / length * 175)
        self.add_tunnel(
            name, None, tip if tip is not None else self.snap(anchor + normal)
        )

    def edit_cage(self, cage):
        dialog = QDialog(self)
        dialog.setWindowTitle("Cage properties")
        form = wrapping_form(dialog)
        number = QLineEdit(cage["name"].removeprefix("cage_"))
        kind = QLineEdit(cage.get("cage_type", "standard"))
        form.addRow("Cage number:", number)
        form.addRow("Cage type:", kind)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        form.addRow(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec():
            name = "cage_" + number.text().strip()
            if not number.text().strip().isdigit() or any(
                c is not cage and c["name"] == name for c in self.graph["cages"]
            ):
                QMessageBox.warning(
                    self, "Cage number", "Use a unique numeric cage number."
                )
                return
            for device in self.devices:
                if device["position"] == cage["name"]:
                    device["position"] = name
                    from deepecohab_recorder.device_antennas import normalize_devices
                    device.update(normalize_devices([device])[0])
            cage["name"], cage["cage_type"] = name, kind.text().strip() or "standard"
            self.defer_draw()

    def edit_tunnel(self, tunnel):
        dialog = QDialog(self)
        dialog.setWindowTitle(tunnel["name"])
        form = wrapping_form(dialog)
        start, end = QComboBox(), QComboBox()
        endpoints = [c["cell_id"] for c in self.graph["cages"]] + [
            j["id"] for j in self.graph["junctions"]
        ]
        for combo in (start, end):
            combo.addItems(endpoints)
        start.setCurrentText(tunnel["start_cell_id"])
        end.setCurrentText(tunnel.get("end_cell_id") or "")
        dead = QCheckBox("Dead-end tunnel")
        dead.setChecked(tunnel.get("dead_end", False))
        end.setEnabled(not dead.isChecked())
        dead.toggled.connect(lambda yes: end.setEnabled(not yes))
        count = QSpinBox()
        from deepecohab_recorder.topology import allows_no_main_antennas
        minimum = 0 if not tunnel['antennas'] and allows_no_main_antennas(tunnel, self.devices) else 1
        count.setRange(minimum, max(2, len(tunnel["antennas"])))
        count.setValue(len(tunnel["antennas"]))
        form.addRow("Start endpoint:", start)
        form.addRow("End endpoint:", end)
        form.addRow(dead)
        form.addRow("Main layout antennas:", count)
        from deepecohab_recorder.ui.tunnel_distances import TunnelDistances
        distances = TunnelDistances(tunnel["antennas"], ([tunnel.get("length")] if len(tunnel["antennas"]) == 2 else tunnel.get("antenna_distances_cm", [])), dialog)
        count.valueChanged.connect(distances.set_antenna_count)
        note = QLabel("Length is the distance between the centres of the two main antennas, in centimetres. Leave an unknown distance as Not set." if len(tunnel["antennas"]) <= 2 else "Review this historical tunnel: it has more than two main antennas. The main config length is null; individual distances are retained in GUI state.")
        note.setWordWrap(True)
        form.addRow(note)
        form.addRow(distances)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        form.addRow(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec():
            before = copy.deepcopy(tunnel)
            tunnel.update(
                start_cell_id=start.currentText(),
                end_cell_id=None if dead.isChecked() else end.currentText(),
                dead_end=dead.isChecked(),
                length=distances.values()[0] if count.value() == 2 else None,
                antenna_distances_cm=distances.values(),
                antennas=(tunnel["antennas"] + ["?"] * count.value())[: count.value()],
            )
            structural = [
                error
                for error in validate_layout(self.graph, devices=self.devices)
                if any(word in error for word in ("Cyclic", "endpoints", "endpoint"))
            ]
            if structural:
                tunnel.clear()
                tunnel.update(before)
                QMessageBox.warning(self, "Invalid connection", "\n".join(structural))
            self.defer_draw()

    def assign_antenna(self, tunnel, index):
        from deepecohab_recorder.ui.antenna_assignment import AntennaAssignmentDialog
        dialog = AntennaAssignmentDialog(self.ui, tunnel["antennas"][index], self, layout=self.graph)
        dialog.setWindowTitle(f"Assign antenna {index + 1} on {tunnel['name']}")
        if dialog.exec():
            source = dialog.value()
            if any(d["antenna"] == source for d in self.devices) or any(
                source == value
                for t in self.graph["tunnels"]
                for i, value in enumerate(t["antennas"])
                if not (t is tunnel and i == index)
            ):
                QMessageBox.warning(
                    self,
                    "Duplicate antenna",
                    f"{source} is already assigned to another antenna node.",
                )
                return
            device = dialog.device_value()
            if device is None:
                tunnel["antennas"][index] = source
            else:
                a, b = self.ends(tunnel)
                device['_editor_position'] = xy(a + (b-a) * ((index+1)/(len(tunnel['antennas'])+1)))
                self.devices.append(device)
                tunnel['antennas'].pop(index)
                tunnel['length'] = None
                tunnel['antenna_distances_cm'] = [None] * max(0, len(tunnel['antennas'])-1)
                from deepecohab_recorder.topology import allows_no_main_antennas
                if not tunnel['antennas'] and allows_no_main_antennas(tunnel, self.devices):
                    self.hint.setText('Device created. This tunnel has no main movement antennas; its device is excluded from interpolation.')
                else:
                    self.hint.setText('Device created. Check the remaining main tunnel antennas; add a replacement in Tunnel properties if needed.')
            self.defer_draw()

    def edit_device(self, device=None):
        from deepecohab_recorder.ui.antenna_assignment import AntennaAssignmentDialog
        dialog = AntennaAssignmentDialog(self.ui, (device or {}).get('antenna', ''), self,
                                         device=device or {}, layout=self.graph, require_slot=True)
        dialog.setWindowTitle('Device antenna')
        if not dialog.exec():
            return
        source, result = dialog.value(), dialog.device_value()
        used = {d['antenna'] for d in self.devices if d is not device} | {a for t in self.graph['tunnels'] for a in t['antennas'] if a != '?'}
        if source in used:
            QMessageBox.warning(self, 'Duplicate antenna', f'{source} is already assigned.')
            return
        if result is None:
            name, index = dialog.main_slot.currentData()
            tunnel = next(t for t in self.graph['tunnels'] if t['name'] == name)
            if index == len(tunnel['antennas']): tunnel['antennas'].append(source)
            else: tunnel['antennas'][index] = source
            tunnel['length'] = None
            tunnel['antenna_distances_cm'] = [None] * max(0, len(tunnel['antennas'])-1)
            if device is not None: self.devices.remove(device)
        elif device is not None:
            device.clear()
            device.update(result)
        else:
            self.devices.append(result)
        self.defer_draw()

    def assign_selected(self):
        selected = self.scene.selectedItems()
        antenna = next(
            (item for item in selected if isinstance(item, AntennaItem)), None
        )
        if antenna:
            self.assign_antenna(antenna.tunnel, antenna.index)
        else:
            self.hint.setText("Select an antenna circle first, or double-click it.")

    def edit_selected(self):
        for item in self.scene.selectedItems():
            if isinstance(item, DeviceAntennaItem):
                self.edit_device(item.device)
                return
            if isinstance(item, CageItem):
                self.edit_cage(item.cage)
                return
            if isinstance(item, TunnelItem):
                self.edit_tunnel(item.tunnel)
                return
            if isinstance(item, AntennaItem):
                self.assign_antenna(item.tunnel, item.index)
                return

    def delete_selected(self):
        selected = self.scene.selectedItems()
        cells = {
            item.cage["cell_id"] for item in selected if isinstance(item, CageItem)
        }
        tunnels = {
            item.tunnel["name"] for item in selected if isinstance(item, TunnelItem)
        }
        tunnels.update(
            t["name"]
            for t in self.graph["tunnels"]
            if t.get("start_cell_id") in cells or t.get("end_cell_id") in cells
        )
        while True:
            junctions = {
                j["id"]
                for j in self.graph["junctions"]
                if j["parent_tunnel"] in tunnels
            }
            added = {
                t["name"]
                for t in self.graph["tunnels"]
                if t.get("start_cell_id") in junctions
                or t.get("end_cell_id") in junctions
            }
            if added <= tunnels:
                break
            tunnels |= added
        deleted_positions = tunnels | {c['name'] for c in self.graph['cages'] if c['cell_id'] in cells}
        devices = {id(item.device) for item in selected if isinstance(item, DeviceAntennaItem)} | {id(d) for d in self.devices if d['position'] in deleted_positions}
        if not cells and not tunnels and not devices:
            self.hint.setText(
                "Select a cage or tunnel to delete it. Antennas are changed in tunnel properties."
            )
            return
        if (
            QMessageBox.question(
                self,
                "Delete layout selection",
                f"Delete {len(cells)} cage(s), {len(tunnels)} tunnel(s) and {len(devices)} device antenna(s), including attached branches?",
            )
            != QMessageBox.Yes
        ):
            return
        self.devices = [d for d in self.devices if id(d) not in devices]
        self.graph["cages"] = [
            c for c in self.graph["cages"] if c["cell_id"] not in cells
        ]
        self.graph["tunnels"] = [
            t for t in self.graph["tunnels"] if t["name"] not in tunnels
        ]
        self.graph["junctions"] = [
            j for j in self.graph["junctions"] if j["parent_tunnel"] not in tunnels
        ]
        self.draw()

    def preset(self):
        if (
            self.graph["cages"]
            and QMessageBox.question(
                self,
                "Four-cage preset",
                "Replace the current layout with a four-cage layout?",
            )
            != QMessageBox.Yes
        ):
            return
        self.graph = preset_layout()
        self.devices = []
        self.align_grid(confirm=False)

    def read(self):
        from deepecohab_recorder.device_antennas import normalize_devices
        normalized = normalize_devices(self.devices, self.ui.hardware_settings()["boards"])
        # Graphics items and an open assignment dialog refer to these objects.
        for source, updated in zip(self.devices, normalized):
            source.clear()
            source.update(updated)
        return {
            "recording.devices": copy.deepcopy(self.devices),
            "recording.layout": normalize_layout(
                self.graph, self.ui.hardware_settings()["boards"]
            ),
        }

    def load(self, data):
        self.devices = copy.deepcopy(data["recording"].get("devices", []))
        self.set_mode("select")
        self.graph = normalize_layout(
            data["recording"]["layout"], data["acquisition"]["boards"]
        )
        self.draw()
        self.fit()
