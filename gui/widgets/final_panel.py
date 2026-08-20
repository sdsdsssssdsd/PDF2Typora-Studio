"""Final / Export control panel."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class FinalPanel(QWidget):
    validate_requested = pyqtSignal()
    freeze_requested = pyqtSignal()
    export_requested = pyqtSignal()
    open_export_dir_requested = pyqtSignal()
    open_typora_requested = pyqtSignal()
    choose_export_dir_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        box = QGroupBox("Final / Export")
        layout = QVBoxLayout(self)
        layout.addWidget(box)
        inner = QVBoxLayout(box)

        self.readiness = QLabel(
            "Readiness:\n"
            "  Transcription: —\n"
            "  Figures: —\n"
            "  Assemble: —\n"
            "  Cleaner: —"
        )
        self.readiness.setWordWrap(True)
        inner.addWidget(self.readiness)

        self.validation = QLabel("Final Validation: Not Run")
        self.validation.setWordWrap(True)
        inner.addWidget(self.validation)

        self.details = QLabel(
            "PAGE: — · FIGURE: — · Images: — · Abs: — · Math: —"
        )
        self.details.setWordWrap(True)
        inner.addWidget(self.details)

        self.final_status = QLabel("Final: —")
        inner.addWidget(self.final_status)

        btn = QHBoxLayout()
        self.validate_btn = QPushButton("运行最终验证")
        self.freeze_btn = QPushButton("生成 final.md")
        self.validate_btn.clicked.connect(self.validate_requested.emit)
        self.freeze_btn.clicked.connect(self.freeze_requested.emit)
        self.freeze_btn.setEnabled(False)
        btn.addWidget(self.validate_btn)
        btn.addWidget(self.freeze_btn)
        inner.addLayout(btn)

        row = QHBoxLayout()
        self.export_path = QLineEdit()
        self.export_path.setPlaceholderText("导出根目录…")
        self.choose_btn = QPushButton("选择")
        self.choose_btn.clicked.connect(self.choose_export_dir_requested.emit)
        row.addWidget(self.export_path)
        row.addWidget(self.choose_btn)
        inner.addLayout(row)

        self.include_pdf = QCheckBox("包含 source.pdf")
        self.include_pdf.setChecked(True)
        inner.addWidget(self.include_pdf)

        exp = QHBoxLayout()
        self.export_btn = QPushButton("导出 Typora 项目")
        self.open_dir_btn = QPushButton("打开导出目录")
        self.typora_btn = QPushButton("用 Typora 打开")
        self.export_btn.setEnabled(False)
        self.open_dir_btn.setEnabled(False)
        self.typora_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export_requested.emit)
        self.open_dir_btn.clicked.connect(self.open_export_dir_requested.emit)
        self.typora_btn.clicked.connect(self.open_typora_requested.emit)
        for b in (self.export_btn, self.open_dir_btn, self.typora_btn):
            exp.addWidget(b)
        inner.addLayout(exp)

        self.status = QLabel("Status: —")
        inner.addWidget(self.status)

        self._validation_pass = False
        self._validation_clean_hash = ""
        self._final_ready = False
        self._last_export_dir: Path | None = None

    def set_export_root(self, path: str) -> None:
        self.export_path.setText(path)

    def export_root(self) -> str:
        return self.export_path.text().strip()

    def include_source_pdf(self) -> bool:
        return self.include_pdf.isChecked()

    def set_project_ready(self, ready: bool) -> None:
        self.validate_btn.setEnabled(ready)

    def set_running(self, running: bool) -> None:
        self.validate_btn.setEnabled(not running)
        if running:
            self.freeze_btn.setEnabled(False)
            self.export_btn.setEnabled(False)
        else:
            self._sync_buttons()

    def update_readiness(self, summary: dict) -> None:
        self.readiness.setText(
            "Readiness:\n"
            f"  Transcription: {'✓' if summary.get('transcription_ready') else '✗'}\n"
            f"  Figures: {'✓' if summary.get('figures_ready') else '✗'}\n"
            f"  Assemble: {'✓' if summary.get('assemble_ready') else '✗'}\n"
            f"  Cleaner: {'✓' if summary.get('clean_ready') else '✗'}\n"
            f"Overall: {summary.get('status', '—')}"
        )
        self.set_project_ready(bool(summary.get("clean_md_exists")))

    def update_validation(
        self,
        *,
        status: str,
        details: dict | None = None,
        clean_hash: str = "",
        stale: bool = False,
    ) -> None:
        label = status
        if stale:
            label = "STALE"
            self._validation_pass = False
        else:
            self._validation_pass = status.upper() in {"PASS", "READY_FOR_FINAL"}
        self._validation_clean_hash = clean_hash
        self.validation.setText(f"Final Validation: {label}")
        d = details or {}
        math = "PASS"
        if d.get("math_warnings") or any(
            "math" in str(b) or "dollar" in str(b) or "aligned" in str(b)
            for b in (d.get("blocking") or [])
        ):
            math = "WARN/FAIL"
        self.details.setText(
            f"PAGE: {d.get('page_markers', '—')} · "
            f"FIGURE: {d.get('figure_markers', '—')} · "
            f"Images: {d.get('image_links_valid', '—')}/{d.get('image_links_total', '—')} · "
            f"Abs: {d.get('absolute_paths', '—')} · "
            f"Math: {math}"
        )
        self._sync_buttons()

    def update_final(self, *, ready: bool, message: str = "") -> None:
        self._final_ready = ready
        self.final_status.setText(f"Final: {'READY' if ready else message or '—'}")
        self._sync_buttons()

    def set_export_result(self, path: Path | None, message: str = "") -> None:
        self._last_export_dir = path
        self.open_dir_btn.setEnabled(path is not None)
        self.typora_btn.setEnabled(path is not None)
        if message:
            self.set_status_message(message)

    def last_export_dir(self) -> Path | None:
        return self._last_export_dir

    def set_status_message(self, msg: str) -> None:
        self.status.setText(f"Status: {msg}")

    def choose_directory(self, start: str = "") -> str | None:
        path = QFileDialog.getExistingDirectory(self, "选择导出根目录", start)
        return path or None

    def _sync_buttons(self) -> None:
        self.freeze_btn.setEnabled(self._validation_pass)
        self.export_btn.setEnabled(self._final_ready and self._validation_pass)
