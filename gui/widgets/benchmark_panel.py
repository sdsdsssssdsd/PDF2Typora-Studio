"""Phase 9.5.2 experimental document-engine benchmark panel."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ai.document_parsers.registry import ENGINE_ORDER, list_engines


class BenchmarkPanel(QWidget):
    run_requested = pyqtSignal()
    open_report_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        box = QGroupBox("Phase 9.5.2 文档引擎 Benchmark（实验）")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(box)
        inner = QVBoxLayout(box)

        self.help = QLabel(
            "同一 PDF 多引擎对比 → 统一 DocumentPageEvidence。\n"
            "不训练、不替换主流程；未安装的引擎会标记 not installed。\n"
            "质量预设：高质量=Native+可选Layout；本面板=实验多引擎。"
        )
        self.help.setWordWrap(True)
        inner.addWidget(self.help)

        self.engine_checks: dict[str, QCheckBox] = {}
        for e in list_engines():
            cb = QCheckBox(
                f"{e['name']} ({e['id']})"
                + ("" if e["available"] else " — 未安装")
            )
            cb.setChecked(e["id"] == "native_pdf" or e["available"])
            if e["id"] == "native_pdf":
                cb.setChecked(True)
            self.engine_checks[e["id"]] = cb
            inner.addWidget(cb)

        row = QHBoxLayout()
        row.addWidget(QLabel("页码范围:"))
        self.pages_edit = QLineEdit("1-8")
        row.addWidget(self.pages_edit)
        inner.addLayout(row)

        btn = QHBoxLayout()
        self.run_btn = QPushButton("运行 Benchmark")
        self.run_btn.setObjectName("PrimaryCta")
        self.open_btn = QPushButton("打开最近报告")
        self.refresh_btn = QPushButton("刷新可用性")
        self.run_btn.clicked.connect(self.run_requested.emit)
        self.open_btn.clicked.connect(self.open_report_requested.emit)
        self.refresh_btn.clicked.connect(self.refresh_availability)
        btn.addWidget(self.run_btn)
        btn.addWidget(self.open_btn)
        btn.addWidget(self.refresh_btn)
        btn.addStretch()
        inner.addLayout(btn)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Benchmark 输出…")
        self.log.setMinimumHeight(120)
        inner.addWidget(self.log)

        self._last_report: str | None = None

    def selected_engines(self) -> list[str]:
        order = list(ENGINE_ORDER)
        selected = [eid for eid, cb in self.engine_checks.items() if cb.isChecked()]
        return sorted(selected, key=lambda x: order.index(x) if x in order else 99)

    def page_range_text(self) -> str:
        return self.pages_edit.text().strip() or "1"

    def refresh_availability(self) -> None:
        for e in list_engines():
            cb = self.engine_checks.get(e["id"])
            if cb is None:
                continue
            label = f"{e['name']} ({e['id']})"
            if not e["available"]:
                label += " — 未安装"
            cb.setText(label)

    def append_log(self, text: str) -> None:
        self.log.appendPlainText(text)

    def set_running(self, running: bool) -> None:
        self.run_btn.setEnabled(not running)

    def set_last_report(self, path: str) -> None:
        self._last_report = path

    def last_report(self) -> str | None:
        return self._last_report
