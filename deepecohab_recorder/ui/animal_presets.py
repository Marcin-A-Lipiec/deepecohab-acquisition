"""Table editors preserve ordinary cells and the cohort's copy-column behavior."""
from PySide6.QtWidgets import (
    QStyledItemDelegate,
)
from deepecohab_recorder.ui.wheel_scroll import (
    QComboBox,
)
from PySide6.QtCore import Qt

GENOTYPES = ['KO', 'WT', 'HET', 'MUT']
BACKGROUNDS = ['C57BL/6J', 'C57BL/6N', 'BALB/cJ', '129S1/SvImJ',
               'DBA/2J', 'C3H/HeJ', 'FVB/NJ', 'CBA/J', 'NOD/ShiLtJ', 'Crl:CD1(ICR)']


class PresetDelegate(QStyledItemDelegate):
    def __init__(self, choices, editable, parent=None):
        super().__init__(parent)
        self.choices, self.editable = choices, editable

    def createEditor(self, parent, option, index):
        editor = QComboBox(parent)
        editor.setEditable(self.editable)
        editor.addItems(self.choices)
        editor.setCurrentIndex(-1)
        editor.setPlaceholderText('Select a value')
        return editor

    def setEditorData(self, editor, index):
        value = index.data(Qt.EditRole) or ''
        editor.setCurrentIndex(editor.findText(value))
        if self.editable:
            editor.setEditText(value)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText().strip(), Qt.EditRole)
