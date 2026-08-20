"""Cleaner review — source vs cleaned side-by-side."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt


class CleanerReview(QWidget):
    accept_cleaned_requested = pyqtSignal(int, str)  # page, text
    keep_source_requested = pyqtSignal(int)
    reprocess_requested = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        box = QGroupBox("Cleaner Review")
        layout = QVBoxLayout(self)
        layout.addWidget(box)
        inner = QVBoxLayout(box)

        self.list = QListWidget()
        self.list.setMaximumHeight(100)
        self.list.currentRowChanged.connect(self._on_row)
        inner.addWidget(self.list)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.source = QPlainTextEdit()
        self.source.setReadOnly(True)
        self.cleaned = QPlainTextEdit()
        left = QVBoxLayout()
        left.addWidget(QLabel("Source (deterministic)"))
        left.addWidget(self.source)
        right = QVBoxLayout()
        right.addWidget(QLabel("Cleaned / Proposal"))
        right.addWidget(self.cleaned)
        lw = QWidget()
        lw.setLayout(left)
        rw = QWidget()
        rw.setLayout(right)
        split.addWidget(lw)
        split.addWidget(rw)
        inner.addWidget(split)

        self.info = QLabel("Validator: —")
        self.info.setWordWrap(True)
        inner.addWidget(self.info)

        btn = QHBoxLayout()
        self.accept_btn = QPushButton("接受清理版")
        self.keep_btn = QPushButton("保留 Source")
        self.edit_btn = QPushButton("编辑后接受")
        self.reprocess_btn = QPushButton("重新清理")
        self.accept_btn.clicked.connect(self._accept)
        self.keep_btn.clicked.connect(self._keep)
        self.edit_btn.clicked.connect(self._edit_accept)
        self.reprocess_btn.clicked.connect(self._reprocess)
        for b in (self.accept_btn, self.keep_btn, self.edit_btn, self.reprocess_btn):
            btn.addWidget(b)
        inner.addLayout(btn)

        self._items: list[dict] = []
        self._current_page: int | None = None

    def set_items(self, items: list[dict]) -> None:
        self._items = items
        self.list.clear()
        for it in items:
            page = int(it["page_number"])
            row = QListWidgetItem(f"⚠ page {page:04d} — {it.get('decision') or it.get('status')}")
            row.setData(Qt.ItemDataRole.UserRole, page)
            self.list.addItem(row)

    def show_page(
        self,
        page: int,
        source: str,
        cleaned: str,
        validator_text: str,
    ) -> None:
        self._current_page = page
        self.source.setPlainText(source)
        self.cleaned.setPlainText(cleaned)
        self.info.setText(validator_text)

    def _on_row(self, row: int) -> None:
        if row < 0 or row >= len(self._items):
            return
        page = int(self._items[row]["page_number"])
        self._current_page = page

    def _accept(self) -> None:
        if self._current_page is None:
            return
        self.accept_cleaned_requested.emit(
            self._current_page, self.cleaned.toPlainText()
        )

    def _edit_accept(self) -> None:
        self._accept()

    def _keep(self) -> None:
        if self._current_page is not None:
            self.keep_source_requested.emit(self._current_page)

    def _reprocess(self) -> None:
        if self._current_page is not None:
            self.reprocess_requested.emit(self._current_page)
