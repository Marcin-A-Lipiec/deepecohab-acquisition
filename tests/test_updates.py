"""Release checks must be explicit, read-only and distinguish unavailable checks."""
import builtins
import io
import json
from urllib.error import HTTPError, URLError

import pytest

from deepecohab_recorder import cli
from deepecohab_recorder.services import updates


def release(version="99.0.0", **changes):
    name = f"deepecohab_acquisition-{version}-py3-none-any.whl"
    result = dict(tag_name=f"v{version}", draft=False, prerelease=False,
        html_url=f"{updates.REPOSITORY}/releases/tag/v{version}",
        assets=[dict(name=name, browser_download_url=f"{updates.REPOSITORY}/releases/download/v{version}/{name}")])
    result.update(changes)
    return result


@pytest.mark.parametrize("installed,latest,status", [
    ("1.0.0", "1.0.1", "update_available"),
    ("1.9.0", "1.10.0", "update_available"),
    ("1.0.0", "1.0.0", "up_to_date"),
    ("1.1.0", "1.0.1", "development"),
])
def test_versions_compare_numerically(installed, latest, status):
    result = updates.parse_release(release(latest), installed)
    assert result.status == status
    assert result.latest_version == latest


@pytest.mark.parametrize("changes", [
    {"tag_name": "v1.0.1-rc1"}, {"tag_name": "main"}, {"tag_name": 12},
    {"prerelease": True}, {"draft": True}, {"prerelease": None},
    {"html_url": "https://example.com/releases/tag/v1.0.1"}, {"assets": None},
])
def test_invalid_or_unstable_release_is_not_offered(changes):
    with pytest.raises(updates.UpdateCheckError):
        updates.parse_release(release(**changes))


def test_missing_duplicate_or_foreign_wheel_is_not_installable():
    valid = release()
    for assets in ([], valid["assets"] * 2, [{**valid["assets"][0], "browser_download_url": "https://example.com/a.whl"}]):
        result = updates.parse_release(release(assets=assets))
        assert result.status == "release_incomplete"
        assert "keep your current installation" in result.message


def test_check_uses_bounded_public_request_only(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPECOHAB_ACQUISITION_HOME", str(tmp_path / "untouched"))
    seen = []
    class Response(io.BytesIO):
        def read(self, size):
            assert size == updates.MAX_RESPONSE_BYTES + 1
            return super().read(size)
    def opener(request, timeout):
        seen.append(request)
        assert request.full_url == updates.API_URL
        assert timeout == updates.TIMEOUT_SECONDS
        assert request.data is None
        assert not request.has_header("Authorization")
        return Response(json.dumps(release()).encode())
    assert updates.check_updates(opener=opener).status == "update_available"
    assert len(seen) == 1
    assert not (tmp_path / "untouched").exists()


@pytest.mark.parametrize("code", [403, 429, 500])
def test_http_failure_is_not_reported_as_up_to_date(code):
    def opener(*args, **kwargs):
        raise HTTPError(updates.API_URL, code, "test", {}, None)
    with pytest.raises(updates.UpdateCheckError):
        updates.check_updates(opener=opener)


def test_no_release_is_not_up_to_date():
    def opener(*args, **kwargs):
        raise HTTPError(updates.API_URL, 404, "test", {}, None)
    assert updates.check_updates(opener=opener).status == "no_release"


@pytest.mark.parametrize("error", [URLError("offline"), TimeoutError("timeout")])
def test_offline_or_timeout_is_explicit(error):
    def opener(*args, **kwargs):
        raise error
    with pytest.raises(updates.UpdateCheckError, match="continues to work offline"):
        updates.check_updates(opener=opener)


@pytest.mark.parametrize("raw", [b"not JSON", b"\xff", b" " * (updates.MAX_RESPONSE_BYTES + 1)],
                         ids=["invalid-json", "invalid-encoding", "oversized"])
def test_bad_or_oversized_response_is_rejected(raw):
    with pytest.raises(updates.UpdateCheckError):
        updates.decode_release(raw)


def test_terminal_check_needs_no_qt_or_camera_import(monkeypatch, capsys):
    original = builtins.__import__
    def importing(name, *args, **kwargs):
        if name.startswith(("PySide6", "PySpin")):
            raise AssertionError("A terminal release check must not load GUI/camera libraries")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", importing)
    monkeypatch.setattr(updates, "check_updates", lambda: updates.parse_release(release()))
    assert cli.main(["updates", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "update_available"


def test_terminal_failure_has_nonzero_status_and_json(monkeypatch, capsys):
    def fail():
        raise updates.UpdateCheckError("offline")
    monkeypatch.setattr(updates, "check_updates", fail)
    assert cli.main(["updates", "--json"]) == 1
    assert json.loads(capsys.readouterr().out) == {"status": "error", "message": "offline"}


def test_gui_check_and_cancel_never_start_or_install_hardware(monkeypatch):
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtNetwork import QNetworkReply
    from PySide6.QtWidgets import QApplication
    from deepecohab_recorder.ui.update_check import UpdateCheckDialog
    qt = QApplication.instance() or QApplication([])
    class Reply(QObject):
        readyRead = Signal()
        finished = Signal()
        def __init__(self, payload):
            super().__init__()
            self.body = bytearray(payload)
            self.aborted = False
        def read(self, count):
            data = bytes(self.body[:count])
            del self.body[:count]
            return data
        def attribute(self, key):
            return 200
        def error(self):
            return QNetworkReply.NoError
        def abort(self):
            self.aborted = True
            self.finished.emit()
    dialog = UpdateCheckDialog()
    requested = []
    first = Reply(json.dumps(release()).encode())
    second = Reply(b"")
    replies = iter((first, second))
    def get(request):
        requested.append(request.url().toString())
        return next(replies)
    monkeypatch.setattr(dialog.network, "get", get)
    assert requested == []
    dialog.check()
    dialog.check()  # A repeated click cannot start another request.
    assert requested == [updates.API_URL]
    assert not dialog.check_button.isEnabled()
    first.readyRead.emit()
    first.finished.emit()
    assert "99.0.0 is available" in dialog.status.text()
    assert dialog.reply is None
    dialog.check()
    dialog.reject()
    assert second.aborted
    assert dialog.reply is None
    assert dialog.check_button.isEnabled()
    dialog.deleteLater()
    qt.processEvents()
