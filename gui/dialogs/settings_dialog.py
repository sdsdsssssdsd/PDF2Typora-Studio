"""Settings dialog stub (Phase 2+)."""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QLabel, QVBoxLayout


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("设置界面将在 Phase 2 实现。"))
