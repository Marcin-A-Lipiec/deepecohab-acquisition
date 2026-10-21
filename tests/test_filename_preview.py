"""The displayed full filename must match the file that saving creates."""
import pytest
from tests.test_diagnostics_and_options import gui

@pytest.mark.parametrize('name', ['2025_11_21_BTBR_M', 'Cohort.v2'])
def test_setup_preview_matches_saved_filename_and_real_extension(gui, name):
    setup = gui.window.widgets['setup']
    setup.experiment_edit.setText(name)
    setup.experiment_edit.editingFinished.emit()
    gui.window.save_section(setup)
    path = gui.window.services.repo.paths()[1]
    assert path.is_file()
    assert path.name == name + '.config.json'
    assert path.stem == name + '.config'
    assert path.suffix == '.json'
    assert setup.target_note.text() == 'Configuration file (full filename): ' + str(path)
    assert 'extensions are added automatically' in setup.experiment_edit.toolTip()
    from deepecohab_recorder import SOFTWARE_LABEL
    assert gui.window.windowTitle() == SOFTWARE_LABEL
