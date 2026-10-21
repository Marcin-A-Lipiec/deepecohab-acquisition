"""A user-requested, asynchronous release check; never installs an update."""
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout

from .. import __version__
from ..services.updates import (
    API_URL, INSTRUCTIONS_URL, MAX_RESPONSE_BYTES, REPOSITORY, TIMEOUT_SECONDS,
    UpdateCheckError, decode_release, no_release,
)


class UpdateCheckDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Check for updates")
        self.resize(530, 280)
        self.network = QNetworkAccessManager(self)
        self.reply = None
        self._body = bytearray()
        self._oversized = False
        self._timed_out = False
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self._timeout)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Installed version: {__version__}"))
        self.status = QLabel("Check GitHub for a published stable release.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.notice = QLabel("Checks run only when requested and need an internet connection. "
            "No experiment data or settings are sent. Your IP address and application version "
            "are visible to GitHub. Updates are installed manually after closing the GUI.")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.check_button = QPushButton("Check again")
        self.check_button.clicked.connect(self.check)
        layout.addWidget(self.check_button)
        self.release_url = REPOSITORY + "/releases"
        self.release_button = QPushButton("Open release notes")
        self.release_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.release_url)))
        layout.addWidget(self.release_button)
        instructions = QPushButton("Open installation and update instructions")
        instructions.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(INSTRUCTIONS_URL)))
        layout.addWidget(instructions)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def check(self):
        if self.reply is not None:
            return
        self._body.clear()
        self._oversized = False
        self._timed_out = False
        self.status.setText("Checking GitHub...")
        self.check_button.setEnabled(False)
        request = QNetworkRequest(QUrl(API_URL))
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"User-Agent", f"DeepEcoHAB-Acquisition/{__version__}".encode("ascii"))
        request.setTransferTimeout(TIMEOUT_SECONDS * 1000)
        self.reply = self.network.get(request)
        self.reply.readyRead.connect(self._read)
        self.reply.finished.connect(self._finished)
        self.timeout.start(TIMEOUT_SECONDS * 1000)

    def _timeout(self):
        if self.reply is not None:
            self._timed_out = True
            self.reply.abort()

    def _read(self):
        if self.reply is None or self._oversized:
            return
        remaining = MAX_RESPONSE_BYTES + 1 - len(self._body)
        self._body.extend(bytes(self.reply.read(remaining)))
        if len(self._body) > MAX_RESPONSE_BYTES:
            self._oversized = True
            self.reply.abort()

    def _finished(self):
        reply = self.reply
        if reply is None:
            return
        self._read()
        try:
            code = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
            if self._oversized:
                raise UpdateCheckError("GitHub's response exceeded the release-check size limit.")
            if self._timed_out:
                raise UpdateCheckError("The update check timed out. Try again later; your installation is unchanged.")
            if code == 404:
                result = no_release()
            elif reply.error() != QNetworkReply.NoError:
                raise UpdateCheckError("Could not check GitHub. Check your internet connection or try again later. "
                    "GitHub may also have rate-limited the request. Your installation is unchanged.")
            else:
                result = decode_release(bytes(self._body))
            self.show_result(result)
        except UpdateCheckError as exc:
            self.status.setText(str(exc))
        finally:
            self.timeout.stop()
            self.reply = None
            reply.deleteLater()
            self.check_button.setEnabled(True)

    def show_result(self, result):
        latest = f"Latest stable version: {result.latest_version}\n\n" if result.latest_version else ""
        self.status.setText(latest + result.message)
        self.release_url = result.release_url

    def cancel(self):
        self.timeout.stop()
        reply, self.reply = self.reply, None
        if reply is not None:
            reply.abort()
            reply.deleteLater()
        self.check_button.setEnabled(True)

    def reject(self):
        self.cancel()
        super().reject()

    def closeEvent(self, event):
        self.cancel()
        super().closeEvent(event)
