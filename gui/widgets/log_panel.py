"""Log panel widget."""

from __future__ import annotations

from PyQt6.QtWidgets import QPlainTextEdit, QVBoxLayout, QWidget


class LogPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(500)
        layout.addWidget(self.text)

    def append(self, message: str) -> None:
        self.text.appendPlainText(message)

    def clear(self) -> None:
        self.text.clear()
