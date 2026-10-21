"""Persistent desktop navigation and experiment information, without device logic."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLayout, QMenu, QPushButton, QScrollArea,
    QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from .. import __version__
from ..preferences import read_preferences, update_section
from .theme import font, icon


class ElidingLabel(QLabel):
    """Keep full text and tooltip while allowing a narrow workspace."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full_text = ""
        self.description = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)
        self.setText(text)

    def setText(self, text):
        self._full_text = str(text)
        self.setToolTip(self._full_text + ("\n" + self.description if self.description else ""))
        self.setAccessibleName(self._full_text)
        self._elide()

    def text(self):
        return self._full_text

    def _elide(self):
        super().setText(self.fontMetrics().elidedText(self._full_text, Qt.ElideMiddle, max(0, self.contentsRect().width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._elide()


class PageStack(QStackedWidget):
    """Mount each page once; stable keys also support existing numeric callers."""

    resized = Signal()

    def __init__(self, specs, parent=None):
        super().__init__(parent)
        self.specs = tuple(specs)
        self.indices = {spec.key: index for index, spec in enumerate(self.specs)}
        if len(self.indices) != len(self.specs):
            raise ValueError("Page keys must be unique.")
        self._icons = {}
        self.setObjectName("pageStack")
        self.layout().setSizeConstraint(QLayout.SetNoConstraint)

    def minimumSizeHint(self):
        return QSize(0, 0)

    def current_key(self):
        index = self.currentIndex()
        return self.specs[index].key if index >= 0 else None

    def set_current_key(self, key):
        self.setCurrentIndex(self.indices[key])

    def page(self, key):
        return self.widget(self.indices[key])

    def tabText(self, index):
        return self.specs[index].title

    def tabIcon(self, index):
        return self._icons[index]

    def setTabIcon(self, index, value):
        self._icons[index] = value

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


class PageViewport(QScrollArea):
    """Let pages with internal tables/canvases scroll as a whole when they cannot fit."""

    def __init__(self, widget):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setWidget(widget)


class NavigationSidebar(QFrame):
    page_requested = Signal(str)
    preference_error = Signal(str)
    COMPACT_BELOW = 1100

    def __init__(self, specs, parent=None):
        super().__init__(parent)
        self.setObjectName("navigationSidebar")
        self.setAccessibleName("Acquisition pages")
        self.specs = tuple(specs)
        self.groups = tuple(dict.fromkeys(spec.group for spec in self.specs))
        self.order = tuple(spec.key for group in self.groups for spec in self.specs
                           if spec.group == group and not spec.pinned_bottom) + tuple(
                               spec.key for spec in self.specs if spec.pinned_bottom)
        self._small = False
        self.collapsed = False
        self.startup_error = None
        try:
            values = read_preferences().get("navigation", {})
            if not isinstance(values, dict) or not isinstance(values.get("collapsed", False), bool):
                raise ValueError("Invalid sidebar preference; using automatic navigation.")
            self.manual_collapsed = values.get("collapsed", False)
        except (ValueError, OSError) as exc:
            self.manual_collapsed = False
            self.startup_error = str(exc)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 12, 8, 8)
        outer.setSpacing(8)
        brand = QHBoxLayout()
        logo = QLabel()
        logo.setObjectName("navigationBrandIcon")
        logo.setFixedSize(40, 40)
        self.logo = logo
        brand.addWidget(logo)
        self.brand = QLabel(f"DeepEcoHab\nAcquisition {__version__}")
        self.brand.setObjectName("navigationBrand")
        brand.addWidget(self.brand, 1)
        outer.addLayout(brand)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("navigationBody")
        items = QVBoxLayout(body)
        items.setContentsMargins(0, 0, 0, 0)
        items.setSpacing(4)
        self.buttons = {}
        self.group_labels = []
        self.menu = QMenu("Acquisition pages", self)
        self.menu_actions = {}

        def add_page(spec, container):
            button = QToolButton()
            button.setObjectName("page_" + spec.key)
            button.setProperty("navigation", True)
            button.setText(spec.title)
            button.setAccessibleName(spec.title)
            button.setToolTip(spec.title)
            button.setCheckable(True)
            button.setFocusPolicy(Qt.StrongFocus)
            button.setIconSize(QSize(20, 20))
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.clicked.connect(lambda checked=False, key=spec.key: self.page_requested.emit(key))
            button.installEventFilter(self)
            container.addWidget(button)
            self.buttons[spec.key] = button
            action = self.menu.addAction(spec.title)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, key=spec.key: self.page_requested.emit(key))
            self.menu_actions[spec.key] = action

        for group in self.groups:
            label = QLabel(group)
            label.setObjectName("navigationGroup")
            items.addWidget(label)
            self.group_labels.append(label)
            self.menu.addSection(group)
            for spec in self.specs:
                if spec.group != group or spec.pinned_bottom:
                    continue
                add_page(spec, items)
        items.addStretch(1)
        self.area.setWidget(body)
        outer.addWidget(self.area, 1)
        if any(spec.pinned_bottom for spec in self.specs):
            self.menu.addSeparator()
            for spec in self.specs:
                if spec.pinned_bottom:
                    add_page(spec, outer)
        self.toggle = QToolButton()
        self.toggle.setObjectName("sidebarToggle")
        self.toggle.setFocusPolicy(Qt.StrongFocus)
        self.toggle.setIconSize(QSize(22, 22))
        self.toggle.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.toggle.clicked.connect(self.toggle_collapsed)
        outer.addWidget(self.toggle)
        self.hint = QLabel("Ctrl+Tab  ·  Next page")
        self.hint.setObjectName("navigationHint")
        outer.addWidget(self.hint)
        self._apply_compact()

    def set_collapsed(self, collapsed, persist=True):
        collapsed = bool(collapsed)
        if persist:
            try:
                update_section("navigation", {"collapsed": collapsed})
            except (ValueError, OSError) as exc:
                self.preference_error.emit(str(exc))
                return False
        self.manual_collapsed = collapsed
        self._apply_compact()
        return True

    def update_available_width(self, width):
        small = width < self.COMPACT_BELOW
        if self._small != small:
            self._small = small
            self._apply_compact()

    def toggle_collapsed(self):
        if self._small:
            # The page picker gives full names without consuming the narrow workspace.
            self.menu.popup(self.toggle.mapToGlobal(self.toggle.rect().topRight()))
        else:
            self.set_collapsed(not self.manual_collapsed)

    def _apply_compact(self):
        self.collapsed = self.manual_collapsed or self._small
        self.setFixedWidth(64 if self.collapsed else 232)
        self.brand.setVisible(not self.collapsed)
        self.hint.setVisible(not self.collapsed)
        for label in self.group_labels:
            label.setVisible(not self.collapsed)
        for button in self.buttons.values():
            button.setToolButtonStyle(Qt.ToolButtonIconOnly if self.collapsed else Qt.ToolButtonTextBesideIcon)
        self.toggle.setToolButtonStyle(Qt.ToolButtonIconOnly if self.collapsed else Qt.ToolButtonTextBesideIcon)
        text = "Choose page" if self._small else ("Expand sidebar" if self.collapsed else "Collapse sidebar")
        self.toggle.setText(text)
        self.toggle.setToolTip(text + " (Ctrl+B)")
        self.toggle.setAccessibleName(text)
        self.apply_theme()

    def select(self, key):
        for candidate, button in self.buttons.items():
            selected = candidate == key
            button.setChecked(selected)
            self.menu_actions[candidate].setChecked(selected)

    def move(self, key, delta):
        target = self.order[(self.order.index(key) + delta) % len(self.order)]
        self.page_requested.emit(target)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.KeyPress and watched in self.buttons.values():
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                watched.click()
                return True
            position = self.order.index(next(key for key, button in self.buttons.items() if button is watched))
            destinations = {Qt.Key_Up: (position - 1) % len(self.order),
                            Qt.Key_Down: (position + 1) % len(self.order),
                            Qt.Key_Home: 0, Qt.Key_End: len(self.order) - 1}
            if event.key() in destinations:
                button = self.buttons[self.order[destinations[event.key()]]]
                button.setFocus(Qt.ShortcutFocusReason)
                if self.area.widget().isAncestorOf(button):
                    self.area.ensureWidgetVisible(button)
                return True
        return super().eventFilter(watched, event)

    def apply_theme(self):
        self.logo.setPixmap(icon("brand", "accent").pixmap(QSize(36, 36)))
        for spec in self.specs:
            symbol = icon(spec.symbol)
            self.buttons[spec.key].setIcon(symbol)
            self.menu_actions[spec.key].setIcon(symbol)
        self.toggle.setIcon(icon("layout-sidebar-left-expand" if self.collapsed else "layout-sidebar-left-collapse"))


class ExperimentHeader(QFrame):
    """Elided identity and last configuration action with a pinned Stop control."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("experimentHeader")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 8, 14, 8)
        outer.setSpacing(4)
        top = QHBoxLayout()
        identity = QVBoxLayout()
        identity.setSpacing(0)
        self.page_title = ElidingLabel()
        self.page_title.setObjectName("currentPageTitle")
        self.experiment = ElidingLabel()
        self.experiment.setObjectName("experimentIdentity")
        identity.addWidget(self.page_title)
        identity.addWidget(self.experiment)
        top.addLayout(identity, 1)
        self.actions_button = QToolButton()
        self.actions_button.setText("Experiment")
        self.actions_button.setAccessibleName("Experiment actions")
        self.actions_button.setToolTip("Load, save or check the experiment; open System check.")
        self.actions_button.setPopupMode(QToolButton.InstantPopup)
        self.actions_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.menu = QMenu(self.actions_button)
        self.actions_button.setMenu(self.menu)
        top.addWidget(self.actions_button)
        self.stop_button = QPushButton("Stop all")
        self.stop_button.setObjectName("stopAllDevices")
        self.stop_button.setProperty("role", "stop")
        self.stop_button.setAccessibleName("Stop all devices")
        self.stop_button.setToolTip("Stop all acquisition devices and flush recorded data. Independently powered lighting and standalone lickometers continue operating.")
        top.addWidget(self.stop_button)
        outer.addLayout(top)
        self.directory = ElidingLabel()
        self.directory.setObjectName("experimentDirectory")
        outer.addWidget(self.directory)
        status = QHBoxLayout()
        self.config_status = ElidingLabel()
        self.config_status.setObjectName("configurationStatus")
        self.config_status.description = "Last configuration action; individual pages may contain unsaved edits."
        self.config_status.setAccessibleDescription(self.config_status.description)
        self.activity = ElidingLabel("All acquisition devices stopped")
        self.activity.setObjectName("deviceStatus")
        status.addWidget(self.config_status, 1)
        status.addWidget(self.activity, 1)
        outer.addLayout(status)
        self._actions = []

    def add_action(self, title, symbol, callback, shortcut=None):
        action = QAction(title, self)
        action.triggered.connect(lambda checked=False: callback())
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
            action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            self.window().addAction(action)
        self.menu.addAction(action)
        self._actions.append((action, symbol))
        return action

    def set_identity(self, name, directory):
        if self.experiment.text() != name:
            self.experiment.setText(name)
        if self.directory.text() != directory:
            self.directory.setText(directory)

    def set_activity(self, text, tone="muted"):
        if self.activity.text() != text:
            self.activity.setText(text)
        if self.activity.property("tone") != tone:
            self.activity.setProperty("tone", tone)
            self.activity.style().unpolish(self.activity)
            self.activity.style().polish(self.activity)

    def apply_theme(self):
        self.page_title.setFont(font(12, bold=True))
        self.actions_button.setIcon(icon("folders"))
        self.stop_button.setIcon(icon("circle-x", "bad"))
        for action, symbol in self._actions:
            action.setIcon(icon(symbol))


def navigation_shortcuts(window, sidebar, pages):
    """Scope shortcuts to this window, leaving form arrow keys and editing intact."""
    shortcuts = []
    for sequence, callback in (
        ("Ctrl+Tab", lambda: sidebar.move(pages.current_key(), 1)),
        ("Ctrl+Shift+Tab", lambda: sidebar.move(pages.current_key(), -1)),
        ("Ctrl+B", sidebar.toggle_collapsed),
    ):
        shortcut = QShortcut(QKeySequence(sequence), window)
        shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        shortcut.activated.connect(callback)
        shortcuts.append(shortcut)
    return shortcuts
