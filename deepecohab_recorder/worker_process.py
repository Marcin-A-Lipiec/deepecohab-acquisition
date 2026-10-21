"""Configure Qt child processes for source and installed launches alike."""
from pathlib import Path
import os

from PySide6.QtCore import QProcessEnvironment

from .runtime_paths import worker_python


def start_module(process, module, arguments, working_directory):
    # Also supports the compatibility source launchers: the parent may have been
    # imported from a checkout without an editable installation. Only the child
    # environment is changed; its cwd is a writable, per-job temporary directory.
    environment = QProcessEnvironment.systemEnvironment()
    package_parent = str(Path(__file__).resolve().parent.parent)
    previous = environment.value("PYTHONPATH")
    environment.insert("PYTHONPATH", package_parent + (os.pathsep + previous if previous else ""))
    environment.insert("PYTHONUNBUFFERED", "1")
    environment.insert("PYTHONIOENCODING", "utf-8")
    process.setProcessEnvironment(environment)
    process.setWorkingDirectory(str(working_directory))
    process.start(worker_python(), ["-m", module, *arguments])


def start_preprocessing(process, arguments, working_directory):
    start_module(process, 'deepecohab_recorder.preprocessing.worker', arguments, working_directory)
