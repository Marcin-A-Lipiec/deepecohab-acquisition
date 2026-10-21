"""Experiment identity and shared metadata, independent of acquisition hardware."""
from pathlib import Path
from zoneinfo import available_timezones
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from deepecohab_recorder.ui.wheel_scroll import (
    QComboBox,
)
from deepecohab_recorder.clock import ExperimentClock
from deepecohab_recorder.runtime_paths import default_experiment_directory
from deepecohab_recorder.experiment_timing import normalize_timing, select_timing
from deepecohab_recorder.experiment_files import experiment_stem, config_path
from deepecohab_recorder.ui.common import help_button, wrapping_form
from deepecohab_recorder.ui.date_time_fields import TemporalPicker


class SetupTab(QWidget):
    paths = ('recording.name', 'recording.project_name', 'recording.recording_location',
             'recording.timeline.recording_timezone', 'recording.timeline.start_from', 'recording.timeline.end_with')

    def __init__(self, ui):
        super().__init__()
        self.ui = ui
        layout = QVBoxLayout(self)
        buttons = QHBoxLayout()
        self.load_button = QPushButton('Load experiment configuration')
        self.save_button = QPushButton('Save experiment configuration')
        self.save_button.setObjectName('primary')
        self.load_button.clicked.connect(self.ui.load_all)
        self.save_button.clicked.connect(self.ui.save_all)
        buttons.addWidget(self.load_button)
        buttons.addWidget(self.save_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        group = QGroupBox('Experiment setup')
        form = wrapping_form(group)
        self.experiment_edit = QLineEdit('experiment')
        self.experiment_edit.setToolTip('Enter only the experiment name, for example 2025_11_21_BTBR_M. File types and extensions are added automatically: .config.json, .data.parquet or .data.csv.')
        self.project_edit = QLineEdit()
        self.project_edit.setPlaceholderText('For example: Tsc2')
        self.directory_edit = QLineEdit(str(default_experiment_directory()))
        self.browse = QPushButton('Browse…')
        self.browse.clicked.connect(self.choose_directory)
        directory_row = QHBoxLayout()
        directory_row.addWidget(self.directory_edit, 1)
        directory_row.addWidget(self.browse)
        self.location = QLineEdit()
        self.location.setPlaceholderText('Building, room, apparatus…')
        self.zone = QComboBox()
        self.zone.setEditable(True)
        self.zone.addItems(sorted(available_timezones()))
        self.zone.setCurrentText('Europe/Warsaw')
        self.start = QComboBox()
        self.start.addItem('Start from the first dark phase', 'dark_phase')
        self.start.addItem('Start from the first light phase', 'light_phase')
        origin = QHBoxLayout()
        origin.addWidget(self.start, 1)
        origin.addWidget(help_button(self, 'Experiment start moment',
            'This defines the analysis origin. If the selected phase is already in progress when recording begins, '
            'its beginning is used and the unrecorded initial portion is marked as missing. Otherwise the next selected phase begins the analysis. '
            'The actual RFID recording start is unchanged. Set phase hours on Environmental settings.'))
        form.addRow('Experiment name:', self.experiment_edit)
        form.addRow('Project name:', self.project_edit)
        form.addRow("Experiment's save directory:", directory_row)
        form.addRow('Recording location:', self.location)
        form.addRow('Experiment timezone:', self.zone)
        form.addRow('Define experiment’s start moment:', origin)
        self.end_with = QComboBox()
        self.end_with.addItem('End with the end of the last dark phase', 'dark_phase')
        self.end_with.addItem('End with the end of the last light phase', 'light_phase')
        self.end_with.setToolTip('Stored as end_with for downstream analysis. This does not stop recording, change recording datetimes or trim preprocessed data.')
        form.addRow('Define experiment’s end moment:', self.end_with)
        layout.addWidget(group)
        times_group = QGroupBox('Experiment recording times')
        times_form = wrapping_form(times_group)
        self.manual_times = QCheckBox('Set start and end datetimes manually (override automatic values)')
        self.manual_times.setToolTip('Unchecked = automatic. Check deliberately to edit both experiment bounds; then save the configuration.')
        times_form.addRow(self.manual_times)
        self.start_datetime = QLineEdit()
        self.end_datetime = QLineEdit()
        self.time_buttons = []
        for label, field in (('Start datetime:', self.start_datetime), ('End datetime:', self.end_datetime)):
            field.setPlaceholderText('YYYY-MM-DDTHH:MM:SS±HH:MM')
            row = QHBoxLayout()
            row.addWidget(field, 1)
            choose = QPushButton('Choose…')
            choose.clicked.connect(lambda checked=False, editor=field: self.choose_time(editor))
            row.addWidget(choose)
            self.time_buttons.append(choose)
            times_form.addRow(label, row)
        self.times_note = QLabel()
        self.times_note.setWordWrap(True)
        times_form.addRow(self.times_note)
        self.manual_times.toggled.connect(self.update_time_controls)
        self.update_time_controls()
        layout.addWidget(times_group)
        self.target_note = QLabel()
        self.target_note.setWordWrap(True)
        self.target_note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.target_note)
        self.help_toggle = QToolButton()
        self.help_toggle.setText('Quick start: new or archived experiment')
        self.help_toggle.setCheckable(True)
        self.help_toggle.setArrowType(Qt.RightArrow)
        self.help_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        layout.addWidget(self.help_toggle, 0, Qt.AlignLeft)
        self.instructions = QLabel(
            '<b>New experiment</b><br>'
            '1. Fill in Setup, then animal details, layout and other experiment settings.<br>'
            '2. Save the experiment configuration before recording.<br>'
            '3. Use Data preprocessing on the raw recordings before downstream analysis.<br><br>'
            '<b>Archived experiment</b><br>'
            '1. Create an experiment folder and a raw_antenna_reads subfolder; copy original raw files into it.<br>'
            '2. For hourly recordings, put each board’s files in its own subfolder. One subfolder means one board’s continuous recording.<br>'
            '3. Data preprocessing detects supported formats. Assign a board number to each old board folder '
            '(1 or 2), and use those labels when assigning layout antennas.<br>'
            '4. Save metadata as a draft at any time. Preprocessing reviews missing details and allows you to proceed at your own risk. Previously preprocessed legacy TXT files are not accepted.<br><br>'
            'Loading a configuration here updates all tabs. Selecting a directory with a compatible configuration loads it automatically. '
            'Always load &lt;experiment_name&gt;.config.json. It automatically loads the linked &lt;experiment_name&gt;.acquisition_gui_state.json from the same folder. '
            'Keep the experiment folder together when moving it. The main file also links to the latest completed &lt;experiment_name&gt;.diagnostic.json.<br><br>'
            '<b>Experiment start and end</b><br>'
            'Automatic mode keeps the first successful RFID board start across all runs in this experiment; starting another board or restarting recording does not replace it. '
            'The end follows the latest normal RFID stop. If recording fails or power is lost, preprocess the complete experiment to recover the end from its last valid RFID detection. '
            'For archives without acquisition logs, raw PC datetimes provide missing bounds. TTLs do not define these bounds.<br>'
            'To override the times, check Set start and end datetimes manually, fill in both fields, and save. '
            'Manual values survive recording and preprocessing; they do not trim the exported data. Uncheck and save to return to the retained automatic values. '
            'Use an explicit UTC offset for ambiguous daylight-saving times.'
        )
        self.instructions.setWordWrap(True)
        self.instructions.setVisible(False)
        layout.addWidget(self.instructions)
        self.help_toggle.toggled.connect(self.toggle_help)
        layout.addStretch(1)
        self.directory_edit.editingFinished.connect(self.ui.directory_changed)
        self.experiment_edit.editingFinished.connect(self.ui.experiment_name_changed)
        self.experiment_edit.textChanged.connect(self.update_target)
        self.directory_edit.textChanged.connect(self.update_target)
        self.zone.activated.connect(self.ui.experiment_timezone_changed)
        self.zone.lineEdit().editingFinished.connect(self.ui.experiment_timezone_changed)
        self.update_target()

    def toggle_help(self, checked):
        self.instructions.setVisible(checked)
        self.help_toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)

    def update_target(self, *args):
        try:
            name = experiment_stem(self.experiment_edit.text())
            target = str(config_path(Path(self.directory_edit.text()).expanduser(), name))
            self.target_note.setText('Configuration file (full filename): ' + target)
            self.target_note.setToolTip(target)
        except ValueError as exc:
            self.target_note.setText(str(exc))

    def choose_directory(self):
        value = QFileDialog.getExistingDirectory(self, "Experiment's save directory", self.directory_edit.text())
        if value:
            self.directory_edit.setText(value)
            self.ui.directory_changed()

    def context_values(self):
        return experiment_stem(self.experiment_edit.text()), self.directory_edit.text().strip()

    def choose_time(self, field):
        try:
            dialog = TemporalPicker(self, field.text(), self.zone.currentText(), True)
            if dialog.exec() == QDialog.Accepted:
                field.setText(dialog.value)
        except ValueError as exc:
            QMessageBox.warning(self, 'Experiment datetime', str(exc))

    def update_time_controls(self):
        manual = self.manual_times.isChecked()
        for widget in (self.start_datetime, self.end_datetime, *self.time_buttons):
            widget.setEnabled(manual)
        self.times_note.setText('Manual overrides will be saved with the experiment and retained during recording and preprocessing.' if manual else
            'Automatic: first RFID start and latest RFID stop across this experiment. Missing bounds are recovered during preprocessing.')

    def load_times(self, timeline):
        timeline = normalize_timing(timeline)
        self.manual_times.setChecked(timeline['datetime_mode'] == 'manual')
        prefix = 'manual_' if self.manual_times.isChecked() else 'automatic_'
        self.start_datetime.setText(timeline[prefix + 'start_datetime'] or '')
        self.end_datetime.setText(timeline[prefix + 'end_datetime'] or '')
        self.update_time_controls()

    def read(self):
        name, _ = self.context_values()
        zone = self.zone.currentText().strip()
        clock = ExperimentClock(zone)
        timing = normalize_timing(self.ui.services.repo.data['recording']['timeline'])
        mode = 'manual' if self.manual_times.isChecked() else 'automatic'
        changes = {'datetime_mode': mode}
        if mode == 'manual':
            for key, editor in (('start_datetime', self.start_datetime), ('end_datetime', self.end_datetime)):
                changes['manual_' + key] = clock.parse(editor.text().strip(), allow_naive=True).isoformat() if editor.text().strip() else None
            select_timing(dict(timing, **changes), require_manual=True)
        return {'recording.name': name, 'recording.project_name': self.project_edit.text().strip(),
                'recording.recording_location': self.location.text().strip(),
                'recording.timeline.recording_timezone': zone, 'recording.timeline.start_from': self.start.currentData(),
                'recording.timeline.end_with': self.end_with.currentData(),
                **{'recording.timeline.' + key: value for key, value in changes.items()}}

    def load(self, config):
        recording = config['recording']
        self.experiment_edit.setText(recording['name'])
        self.project_edit.setText(recording.get('project_name', ''))
        self.location.setText(recording.get('recording_location', ''))
        self.zone.setCurrentText(recording['timeline']['recording_timezone'])
        self.start.setCurrentIndex(max(0, self.start.findData(recording['timeline']['start_from'])))
        self.end_with.setCurrentIndex(max(0, self.end_with.findData(recording['timeline'].get('end_with', 'dark_phase'))))
        self.load_times(recording['timeline'])
        self.update_target()

    def refresh(self):
        services = self.ui.services
        processing = self.ui.background_busy()
        locked = services.busy() or bool(services.ports.snapshot()) or services.lighting.busy or processing
        for widget in (self.experiment_edit, self.directory_edit, self.browse, self.start, self.load_button, self.manual_times):
            widget.setEnabled(not locked)
        for widget in (self.start_datetime, self.end_datetime, *self.time_buttons):
            widget.setEnabled(not locked and self.manual_times.isChecked())
        if not self.manual_times.isChecked():
            timing = normalize_timing(services.repo.data['recording']['timeline'])
            self.start_datetime.setText(timing['automatic_start_datetime'] or '')
            self.end_datetime.setText(timing['automatic_end_datetime'] or '')
        anchored = bool(services.repo.data['recording']['timeline'].get('phase_schedule', {}).get('anchor_datetime'))
        self.zone.setEnabled(not locked and not anchored and not services.lighting.connected)
        self.save_button.setEnabled(not processing)
