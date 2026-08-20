"""Import panel widget."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class DropZone(QFrame):
    file_dropped = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Plain)
        self.setMinimumHeight(160)

        layout = QVBoxLayout(self)
        self._label = QLabel("拖放 PDF 到这里\n或点击下方「导入 PDF」")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("font-size: 15px; color: #4a5751; line-height: 1.4;")
        layout.addWidget(self._label)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].toLocalFile().lower().endswith(".pdf"):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path.lower().endswith(".pdf"):
                self.file_dropped.emit(path)
                event.acceptProposedAction()
                return
        event.ignore()


class ImportPanel(QWidget):
    import_requested = pyqtSignal()
    pdf_selected = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)

        self.drop_zone = DropZone()
        self.drop_zone.file_dropped.connect(self.pdf_selected.emit)
        layout.addWidget(self.drop_zone)

        btn_row = QVBoxLayout()
        self.import_btn = QPushButton("导入 PDF")
        self.import_btn.setObjectName("PrimaryCta")
        self.import_btn.setMinimumHeight(44)
        self.import_btn.clicked.connect(self.import_requested.emit)
        btn_row.addWidget(self.import_btn)
        layout.addLayout(btn_row)

        info_frame = QFrame()
        info_frame.setFrameStyle(QFrame.Shape.StyledPanel)
        grid = QGridLayout(info_frame)
        grid.addWidget(QLabel("文件："), 0, 0)
        grid.addWidget(QLabel("页数："), 1, 0)
        grid.addWidget(QLabel("大小："), 2, 0)
        grid.addWidget(QLabel("项目："), 3, 0)
        grid.addWidget(QLabel("状态："), 4, 0)

        self.file_label = QLabel("—")
        self.pages_label = QLabel("—")
        self.size_label = QLabel("—")
        self.project_label = QLabel("—")
        self.status_label = QLabel("等待导入")

        for lbl in (
            self.file_label,
            self.pages_label,
            self.size_label,
            self.project_label,
            self.status_label,
        ):
            lbl.setWordWrap(True)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        grid.addWidget(self.file_label, 0, 1)
        grid.addWidget(self.pages_label, 1, 1)
        grid.addWidget(self.size_label, 2, 1)
        grid.addWidget(self.project_label, 3, 1)
        grid.addWidget(self.status_label, 4, 1)

        layout.addWidget(info_frame)

    def set_pdf_info(
        self,
        file_name: str,
        page_count: int,
        file_size: int,
        project_path: str = "",
        status: str = "",
    ) -> None:
        self.file_label.setText(file_name)
        self.pages_label.setText(str(page_count))
        self.size_label.setText(self._format_size(file_size))
        if project_path:
            self.project_label.setText(project_path)
        if status:
            self.status_label.setText(status)

    @staticmethod
    def _format_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.2f} MB"

    def reset(self) -> None:
        self.file_label.setText("—")
        self.pages_label.setText("—")
        self.size_label.setText("—")
        self.project_label.setText("—")
        self.status_label.setText("等待导入")
