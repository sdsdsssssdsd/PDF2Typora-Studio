"""Figure batch control panel."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class FigurePanel(QWidget):
    start_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    open_review_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        box = QGroupBox("Figure Pipeline")
        layout = QVBoxLayout(self)
        layout.addWidget(box)
        inner = QVBoxLayout(box)

        form = QFormLayout()
        self.dpi_combo = QComboBox()
        for dpi in (200, 250, 300, 400):
            self.dpi_combo.addItem(str(dpi), dpi)
        self.dpi_combo.setCurrentIndex(2)
        form.addRow("Crop DPI:", self.dpi_combo)

        self.auto_resolve = QCheckBox("自动解析高置信度 Figure")
        self.auto_resolve.setChecked(True)
        form.addRow("", self.auto_resolve)

        self.analyze_only = QCheckBox("仅分析（不写 resolved_pages）")
        form.addRow("", self.analyze_only)
        inner.addLayout(form)

        btn = QHBoxLayout()
        self.start_btn = QPushButton("分析 Figures")
        self.cancel_btn = QPushButton("取消")
        self.review_btn = QPushButton("打开 Figure Review")
        self.cancel_btn.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self.start_requested.emit)
        self.cancel_btn.clicked.connect(self.cancel_requested.emit)
        self.review_btn.clicked.connect(self.open_review_requested.emit)
        btn.addWidget(self.start_btn)
        btn.addWidget(self.cancel_btn)
        btn.addWidget(self.review_btn)
        btn.addStretch()
        inner.addLayout(btn)

        self.progress = QProgressBar()
        inner.addWidget(self.progress)
        self.status = QLabel("需要 canonical 转录结果才能分析 Figures。")
        inner.addWidget(self.status)
        self.stats = QLabel("Native: 0 · Clip: 0 · Review: 0 · Failed: 0")
        inner.addWidget(self.stats)
        self.readiness = QLabel("Figure Readiness: —")
        inner.addWidget(self.readiness)

    def selected_dpi(self) -> int:
        v = self.dpi_combo.currentData()
        return int(v) if v else 300

    def is_analyze_only(self) -> bool:
        return self.analyze_only.isChecked()

    def set_project_ready(self, ready: bool) -> None:
        self.start_btn.setEnabled(ready)

    def set_running(self, running: bool) -> None:
        self.start_btn.setEnabled(not running)
        self.cancel_btn.setEnabled(running)

    def update_stats(
        self,
        *,
        native: int = 0,
        clip: int = 0,
        review: int = 0,
        failed: int = 0,
        message: str = "",
    ) -> None:
        self.stats.setText(
            f"Native: {native} · Clip: {clip} · Review: {review} · Failed: {failed}"
        )
        if message:
            self.status.setText(message)

    def update_readiness(self, summary: dict) -> None:
        ready = summary.get("ready")
        label = "Ready" if ready else "Not Ready"
        self.readiness.setText(
            f"Figure Readiness: {label} · "
            f"Resolved {summary.get('resolved', 0)}/"
            f"{summary.get('figures_total', 0)} · "
            f"Remaining Reviews: {summary.get('remaining_reviews', 0)}"
        )
