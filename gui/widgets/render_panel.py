"""PDF render control panel."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


class RenderPanel(QWidget):
    start_requested = pyqtSignal()
    cancel_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        box = QGroupBox("PDF 页面渲染")
        layout = QVBoxLayout(self)
        layout.addWidget(box)
        inner = QVBoxLayout(box)

        form = QFormLayout()
        self.dpi_combo = QComboBox()
        self.dpi_combo.setEditable(True)
        for dpi, tip in (
            (150, "150 — 快速"),
            (200, "200 — 推荐"),
            (250, "250 — 公式/小字"),
            (300, "300 — 高质量"),
        ):
            self.dpi_combo.addItem(tip, dpi)
        self.dpi_combo.setCurrentIndex(1)
        form.addRow("DPI:", self.dpi_combo)

        page_row = QVBoxLayout()
        self.radio_all = QRadioButton("全部页面")
        self.radio_custom = QRadioButton("自定义")
        self.radio_all.setChecked(True)
        self.page_group = QButtonGroup(self)
        self.page_group.addButton(self.radio_all)
        self.page_group.addButton(self.radio_custom)
        self.range_edit = QLineEdit()
        self.range_edit.setPlaceholderText("例如 1-20 或 1,3,5-10")
        self.range_edit.setEnabled(False)
        self.radio_custom.toggled.connect(self.range_edit.setEnabled)
        page_row.addWidget(self.radio_all)
        page_row.addWidget(self.radio_custom)
        page_row.addWidget(self.range_edit)
        form.addRow("页面:", page_row)
        inner.addLayout(form)

        tip = QLabel("150 快速 · 200 推荐 · 250 公式/小字 · 300 高质量")
        tip.setStyleSheet("color: #666; font-size: 11px;")
        inner.addWidget(tip)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("开始渲染")
        self.start_btn.setObjectName("PrimaryCta")
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self.start_requested.emit)
        self.cancel_btn.clicked.connect(self.cancel_requested.emit)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addStretch()
        inner.addLayout(btn_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        inner.addWidget(self.progress)

        self.status_label = QLabel("等待导入 PDF")
        self.current_label = QLabel("")
        inner.addWidget(self.status_label)
        inner.addWidget(self.current_label)

        self._has_project = False

    def set_project_ready(self, ready: bool) -> None:
        self._has_project = ready
        self.start_btn.setEnabled(ready and not self.cancel_btn.isEnabled())
        if not ready:
            self.status_label.setText("等待导入 PDF")

    def set_rendering(self, rendering: bool) -> None:
        self.cancel_btn.setEnabled(rendering)
        self.start_btn.setEnabled(self._has_project and not rendering)

    def selected_dpi(self) -> int:
        data = self.dpi_combo.currentData()
        if isinstance(data, int):
            return data
        text = self.dpi_combo.currentText().strip().split()[0]
        return int(text)

    def page_range_expression(self) -> str | None:
        if self.radio_all.isChecked():
            return None
        return self.range_edit.text().strip()

    def update_progress(self, done: int, total: int, message: str) -> None:
        if total > 0:
            self.progress.setValue(int(done * 100 / total))
        self.status_label.setText(message)
        self.current_label.setText(f"进度：{done} / {total}")

    def reset_progress(self) -> None:
        self.progress.setValue(0)
        self.current_label.setText("")
