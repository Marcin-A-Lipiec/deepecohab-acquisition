"""Layout defaults and explicit overrides share device destinations across input types."""
import copy
import re
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)
from deepecohab_recorder.ui.wheel_scroll import (
    QComboBox,
)
from deepecohab_recorder.preprocessing.output_plan import data_stem, normalize_groups
from deepecohab_recorder.device_antennas import layout_defaults


def action(label, callback, hint):
    control = QPushButton(label)
    control.setToolTip(hint)
    control.clicked.connect(callback)
    return control


class GroupingPage(QWidget):
    def __init__(self, dialog, sources, defaults, overrides, ttl=False):
        super().__init__()
        self.dialog, self.owner = dialog, dialog.owner
        self.sources, self.defaults, self.routes = sorted(sources), defaults, dict(overrides)
        self.ttl = ttl
        self.default = 'TTL_data1' if ttl else 'main'
        self.prefix = 'TTL_data' if ttl else 'device_antenna'
        layout = QVBoxLayout(self)
        note = QLabel('Use Layout default to follow device associations. Choose a destination to override this input independently. TTL events can share a device file or use separate TTL files; they never enter main movement data.' if ttl else
                      'Layout device names define default groups. Main antennas default to movement data. Explicit overrides are preserved when the layout changes; physical device antennas never train movement interpolation.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(len(self.sources), 4)
        self.table.setHorizontalHeaderLabels(['Input', 'Layout default', 'Destination override', 'Output filename'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 110)
        self.table.setColumnWidth(1, 230)
        self.table.setWordWrap(False)
        self.table.setColumnWidth(2, 245)
        for row, source in enumerate(self.sources):
            for col, text in ((0, source), (1, self.label(self.defaults.get(source, self.default))), (3, '')):
                cell = QTableWidgetItem(text)
                cell.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                cell.setToolTip(text)
                self.table.setItem(row, col, cell)
            choice = QComboBox()
            self.table.setCellWidget(row, 2, choice)
            self.populate(choice, self.routes.get(source))
            choice.currentIndexChanged.connect(self.preview)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        buttons.addWidget(action('Select all', self.table.selectAll, 'Select every input on this page.'))
        buttons.addWidget(action('Restore layout defaults', lambda: self.assign(None), 'Remove overrides for selected inputs.'))
        buttons.addWidget(action('Group selected together', self.group_together, 'Create a numbered group for the selection.'))
        buttons.addWidget(action('One file per input', self.separate, 'Create a numbered group for each selected input.'))
        layout.addLayout(buttons)
        self.note = QLabel()
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        self.preview()
        self.selection_changed()

    def label(self, group):
        return 'Main movement data' if group == 'main' else group + (' — ' + self.dialog.names[group] if group in self.dialog.names else '')

    def populate(self, choice, group):
        choice.blockSignals(True)
        choice.clear()
        choice.addItem('Layout default', None)
        if not self.ttl: choice.addItem('Main movement data (override)', 'main')
        for name in sorted(self.dialog.groups, key=lambda g: (g.startswith('TTL'), int(re.search(r'\d+$', g)[0]))):
            if self.ttl or name.startswith('device_antenna'):
                choice.addItem(self.label(name), name)
        choice.setCurrentIndex(max(0, choice.findData(group)))
        choice.blockSignals(False)

    def selected_rows(self):
        return sorted({i.row() for i in self.table.selectedIndexes()})

    def selection_changed(self):
        self.note.setText(f'{len(self.selected_rows())} of {len(self.sources)} inputs selected.' if self.sources else 'No configured or observed inputs of this type.')

    def assign(self, group):
        if group is not None and group != 'main':
            if not re.fullmatch(r'(?:device_antenna|TTL_data)[1-9]\d*', group):
                raise ValueError('Choose a numbered destination.')
            self.dialog.groups.add(group)
        if self.ttl and group == 'main': raise ValueError('TTL events cannot enter movement data.')
        for row in self.selected_rows(): self.populate(self.table.cellWidget(row, 2), group)
        self.dialog.refresh_choices()

    def next_group(self, used):
        n = 1
        while f'{self.prefix}{n}' in used: n += 1
        return f'{self.prefix}{n}'

    def group_together(self):
        if self.selected_rows(): self.assign(self.next_group(self.dialog.groups))

    def separate(self):
        for row in self.selected_rows():
            group = self.next_group(self.dialog.groups)
            self.dialog.groups.add(group)
            self.populate(self.table.cellWidget(row, 2), group)
        self.dialog.refresh_choices()

    def refresh_choices(self):
        for row in range(len(self.sources)):
            choice = self.table.cellWidget(row, 2)
            self.populate(choice, choice.currentData())
        self.preview()

    def preview(self, *_):
        name = self.owner.output_name()
        parts = len({s.part for s in self.owner.input_specs()}) if self.owner.options['split_restarts'] else 1
        extensions = [e for e, box in (('parquet', self.owner.parquet), ('csv', self.owner.csv)) if box.isChecked()] or ['parquet']
        for row, source in enumerate(self.sources):
            override = self.table.cellWidget(row, 2).currentData()
            group = override or self.defaults.get(source, self.default)
            text = ' / '.join(data_stem(name, group, 1, parts) + '.' + ext for ext in extensions)
            self.table.item(row, 3).setText(text)
            warning = 'Device role retained: this custom main output does not make the device a movement antenna.' if not self.ttl and source in self.dialog.devices and group == 'main' else text
            self.table.item(row, 3).setToolTip(warning)
        self.selection_changed()

    def values(self):
        result = {a: g for a, g in self.routes.items() if a not in self.sources}
        for row, source in enumerate(self.sources):
            value = self.table.cellWidget(row, 2).currentData()
            if value is not None: result[source] = value
        return result


class OutputGroupsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.owner = parent
        self.setWindowTitle('Manage RFID and TTL output grouping')
        self.resize(1100, 650)
        config = parent.grouping_config() if hasattr(parent, 'grouping_config') else parent.read_config()
        options = normalize_groups(copy.deepcopy(parent.options))
        defaults, ttl_defaults, self.names, self.conflicts = layout_defaults(config, options, strict=False)
        self.registry = options['device_groups']
        self.devices = {d['antenna'] for d in config['recording'].get('devices', [])}
        self.groups = (set(self.registry.values()) | set(options['routes'].values()) | set(options['ttl_routes'].values()) | {'TTL_data1'}) - {'main', 'rfid'}
        layout = QVBoxLayout(self)
        tabs = self.tabs = QTabWidget()
        layout.addWidget(tabs)
        main = {a for t in config['recording']['layout'].get('tunnels', []) for a in t.get('antennas', []) if a != '?'}
        rfids = parent.antenna_names() | main | set(defaults) | set(options['routes'])
        ttls = set(parent._used_ttl) | set(ttl_defaults) | set(self.conflicts) | set(options['ttl_routes']) | set(config.get('acquisition', {}).get('active_inputs', {}).get('ttl', []))
        self.rfid = GroupingPage(self, rfids, defaults, options['routes'])
        self.ttl = GroupingPage(self, ttls, ttl_defaults, options['ttl_routes'], ttl=True)
        tabs.addTab(self.rfid, 'RFID antennas')
        tabs.addTab(self.ttl, 'TTL inputs')
        self.sources, self.table = self.rfid.sources, self.rfid.table
        self.windows = options.get('group_windows_ms', {})
        reset = action('Restore all layout defaults', self.reset_all, 'Remove all explicit input destination overrides.')
        layout.addWidget(reset)
        note = QLabel('Device and TTL destinations are shared across both pages. Only groups with records create files. The README identifies each numbered file and its actual inputs. Save layout changes before preprocessing; unsaved edits are previewed here.')
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('Apply grouping')
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def validate_and_accept(self):
        missing = set(self.conflicts) - self.ttl_values().keys()
        if missing:
            QMessageBox.warning(self, 'Resolve shared TTL inputs', 'Select an explicit destination for: ' + ', '.join(sorted(missing)))
            return
        self.accept()

    def refresh_choices(self):
        for key in ('rfid', 'ttl'):
            if hasattr(self, key): getattr(self, key).refresh_choices()

    def reset_all(self):
        for page in (self.rfid, self.ttl):
            page.routes.clear()
            for row in range(len(page.sources)): page.populate(page.table.cellWidget(row, 2), None)
            page.preview()

    def values(self): return self.rfid.values()
    def ttl_values(self): return self.ttl.values()
    def stitch_windows(self): return dict(self.windows)
