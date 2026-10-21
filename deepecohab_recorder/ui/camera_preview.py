"""GUI-owned preview cache; never shares the capture thread's frame memory."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap


class PreviewCache:
    def __init__(self):
        self.controller = None
        self.sequence = None
        self.image = None
        self.size = None

    def update(self, controller, label):
        if controller is not self.controller:
            self.controller = controller
            self.sequence = None
            self.image = None
            self.size = None
        snapshot = getattr(controller, "get_preview_snapshot", None)
        if snapshot is None:
            frame = controller.get_preview_frame()  # Compatibility with older adapters.
        else:
            self.sequence, frame = snapshot(self.sequence)
        changed = frame is not None
        if changed:
            height, width = frame.shape[:2]
            self.image = QImage(frame.data, width, height, frame.strides[0],
                QImage.Format_Grayscale8).copy()
        size = label.size()
        if self.image is not None and (changed or size != self.size):
            label.setPixmap(QPixmap.fromImage(self.image).scaled(
                size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.size = size
