"""Dispose deferred Qt wrappers between tests, outside native model/style work."""
import gc
import sys
import pytest


def release_deferred_qt_objects():
    qt = sys.modules.get('PySide6.QtCore')
    if qt is not None and qt.QCoreApplication.instance() is not None:
        qt.QCoreApplication.sendPostedEvents(None, qt.QEvent.DeferredDelete)
        gc.collect()


@pytest.fixture(autouse=True)
def release_qt_objects_between_tests():
    release_deferred_qt_objects()
    yield
    release_deferred_qt_objects()
