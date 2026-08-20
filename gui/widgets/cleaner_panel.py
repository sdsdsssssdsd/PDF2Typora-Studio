"""Markdown Cleaner control panel."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class CleanerPanel(QWidget):
    analyze_requested = pyqtSignal()
    start_requested = pyqtSignal()
    pause_requested = pyqtSignal()
    resume_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    open_clean_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        box = QGroupBox("Markdown Cleaner")
        layout = QVBoxLayout(self)
        layout.addWidget(box)
        inner = QVBoxLayout(box)

        row = QHBoxLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("SMART", "smart")
        self.mode_combo.addItem("SAFE_RULES_ONLY", "safe_rules_only")
        self.mode_combo.addItem("FULL_AI", "full_ai")
        row.addWidget(QLabel("Mode:"))
        row.addWidget(self.mode_combo)
        inner.addLayout(row)

        self.analysis = QLabel("Analyze 后显示 Rules-only / AI-needed…")
        self.analysis.setWordWrap(True)
        inner.addWidget(self.analysis)

        self.readiness = QLabel("Clean Readiness: —")
        inner.addWidget(self.readiness)

        btn = QHBoxLayout()
        self.analyze_btn = QPushButton("分析格式问题")
        self.start_btn = QPushButton("开始清洗")
        self.pause_btn = QPushButton("暂停")
        self.resume_btn = QPushButton("继续")
        self.cancel_btn = QPushButton("取消")
        self.open_btn = QPushButton("打开 clean.md")
        self.start_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.analyze_btn.clicked.connect(self.analyze_requested.emit)
        self.start_btn.clicked.connect(self.start_requested.emit)
        self.pause_btn.clicked.connect(self.pause_requested.emit)
        self.resume_btn.clicked.connect(self.resume_requested.emit)
        self.cancel_btn.clicked.connect(self.cancel_requested.emit)
        self.open_btn.clicked.connect(self.open_clean_requested.emit)
        for b in (
            self.analyze_btn,
            self.start_btn,
            self.pause_btn,
            self.resume_btn,
            self.cancel_btn,
            self.open_btn,
        ):
            btn.addWidget(b)
        inner.addLayout(btn)

        self.stats = QLabel(
            "Rule: 0 · AI: 0 · Review: 0 · Failed: 0 · Cached: 0"
        )
        inner.addWidget(self.stats)
        self.status = QLabel("Status: —")
        inner.addWidget(self.status)

    def selected_mode(self) -> str:
        return str(self.mode_combo.currentData() or "smart")

    def set_project_ready(self, ready: bool) -> None:
        self.analyze_btn.setEnabled(ready)
        self.start_btn.setEnabled(ready)

    def set_running(self, running: bool) -> None:
        self.start_btn.setEnabled(not running)
        self.analyze_btn.setEnabled(not running)
        self.pause_btn.setEnabled(running)
        self.cancel_btn.setEnabled(running)
        self.resume_btn.setEnabled(False)

    def set_paused(self, paused: bool) -> None:
        self.pause_btn.setEnabled(not paused)
        self.resume_btn.setEnabled(paused)

    def update_analysis(self, summary: dict) -> None:
        self.analysis.setText(
            f"Pages: {summary.get('pages', 0)}\n"
            f"Already Clean: {summary.get('already_clean', 0)}\n"
            f"Needs Rule Fix: {summary.get('needs_rule_fix', 0)}\n"
            f"Needs AI: {summary.get('needs_ai', 0)}"
        )

    def update_stats(self, summary: dict) -> None:
        self.stats.setText(
            f"Rule: {summary.get('rule_cleaned', 0)} · "
            f"AI: {summary.get('ai_cleaned', 0)} · "
            f"Review: {summary.get('needs_review', 0)} · "
            f"Failed: {summary.get('failed', 0)} · "
            f"Cached: {summary.get('cached', 0)}"
        )

    def update_readiness(self, summary: dict) -> None:
        self.readiness.setText(
            f"Clean Readiness: {summary.get('label', '—')} · "
            f"{summary.get('success', 0)}/{summary.get('pages', 0)}"
        )

    def set_status_message(self, msg: str) -> None:
        self.status.setText(f"Status: {msg}")
