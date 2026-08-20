"""Review queue with Transcription and Figures tabs (Phase 6.5)."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

_USER_ROLE = Qt.ItemDataRole.UserRole


class _TranscriptionTab(QWidget):
    page_selected = pyqtSignal(int)
    accept_requested = pyqtSignal(int, str)
    skip_requested = pyqtSignal(int)
    retranscribe_requested = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.filter_combo = QComboBox()
        for label, key in (
            ("全部待审/失败", "all"),
            ("Needs Review", "needs_review"),
            ("Failed", "failed"),
            ("Prompt Leak", "prompt_leak"),
            ("Figure", "figure"),
            ("Formula", "formula"),
            ("Table", "table"),
            ("Timeout", "timeout"),
            ("Context", "context"),
        ):
            self.filter_combo.addItem(label, key)
        layout.addWidget(self.filter_combo)

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_row)
        layout.addWidget(self.list)

        self.issues = QLabel("Issues: —")
        self.issues.setWordWrap(True)
        layout.addWidget(self.issues)

        self.attempt_combo = QComboBox()
        layout.addWidget(self.attempt_combo)

        self.markdown = QPlainTextEdit()
        self.markdown.setPlaceholderText("选择待审页以查看 Markdown…")
        layout.addWidget(self.markdown, stretch=1)

        btn = QHBoxLayout()
        self.retranscribe_btn = QPushButton("重新转录")
        self.accept_btn = QPushButton("接受")
        self.edit_accept_btn = QPushButton("编辑后接受")
        self.skip_btn = QPushButton("跳过")
        self.retranscribe_btn.clicked.connect(self._retranscribe)
        self.accept_btn.clicked.connect(lambda: self._accept(False))
        self.edit_accept_btn.clicked.connect(lambda: self._accept(True))
        self.skip_btn.clicked.connect(self._skip)
        for b in (
            self.retranscribe_btn,
            self.accept_btn,
            self.edit_accept_btn,
            self.skip_btn,
        ):
            btn.addWidget(b)
        layout.addLayout(btn)

        self._pages: list[dict] = []
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)

    def set_items(self, items: list[dict]) -> None:
        self._pages = items
        self._apply_filter()

    def _apply_filter(self) -> None:
        key = self.filter_combo.currentData()
        self.list.clear()
        for item in self._pages:
            err = (item.get("error_message") or "") + " " + (item.get("error_code") or "")
            status = item.get("status") or ""
            if key == "needs_review" and status != "needs_review":
                continue
            if key == "failed" and status != "failed":
                continue
            if key == "prompt_leak" and "prompt_leak" not in err:
                continue
            if key == "figure" and "figure" not in err.lower():
                continue
            if key == "formula" and "formula" not in err.lower():
                continue
            if key == "table" and "table" not in err.lower():
                continue
            if key == "timeout" and "timeout" not in err.lower():
                continue
            if key == "context" and "context" not in err.lower():
                continue
            page = int(item["page_number"])
            icon = "⚠" if status == "needs_review" else "✗"
            row = QListWidgetItem(f"{icon} {page:04d}")
            row.setData(_USER_ROLE, page)
            self.list.addItem(row)

    def show_page_detail(
        self,
        page_number: int,
        issues: str,
        markdown: str,
        attempts: list[tuple[str, Path]],
    ) -> None:
        self.issues.setText(f"Issues: {issues or '—'}")
        self.markdown.setPlainText(markdown)
        self.attempt_combo.clear()
        for label, path in attempts:
            self.attempt_combo.addItem(label, str(path))

    def current_page(self) -> int | None:
        item = self.list.currentItem()
        if item is None:
            return None
        page = item.data(_USER_ROLE)
        return int(page) if page is not None else None

    def _on_row(self, row: int) -> None:
        if row < 0:
            return
        item = self.list.item(row)
        page = item.data(_USER_ROLE)
        if isinstance(page, int):
            self.page_selected.emit(page)

    def _accept(self, edited: bool) -> None:
        page = self.current_page()
        if page is None:
            return
        self.accept_requested.emit(page, self.markdown.toPlainText() if edited else "")

    def _skip(self) -> None:
        page = self.current_page()
        if page is not None:
            self.skip_requested.emit(page)

    def _retranscribe(self) -> None:
        page = self.current_page()
        if page is not None:
            self.retranscribe_requested.emit(page)


class _FiguresTab(QWidget):
    figure_selected = pyqtSignal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.filter_combo = QComboBox()
        for label, key in (
            ("全部", "all"),
            ("missing_marker", "missing_marker"),
            ("marker_index_conflict", "marker_index_conflict"),
            ("marker_mismatch", "marker_mismatch"),
            ("no_bbox", "no_bbox"),
            ("manual_crop_required", "manual_crop_required"),
        ):
            self.filter_combo.addItem(label, key)
        layout.addWidget(self.filter_combo)

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_row)
        layout.addWidget(self.list)

        self.detail = QLabel("选择 Figure 问题项…")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)

        self.open_btn = QPushButton("打开 Figure Review")
        self.open_btn.clicked.connect(self._open)
        layout.addWidget(self.open_btn)

        self._items: list[dict] = []
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)

    def set_items(self, items: list[dict]) -> None:
        self._items = items
        self._apply_filter()

    def _apply_filter(self) -> None:
        key = self.filter_combo.currentData()
        self.list.clear()
        for item in self._items:
            warnings = item.get("warnings") or "[]"
            if isinstance(warnings, str):
                import json

                try:
                    warn_list = json.loads(warnings)
                except json.JSONDecodeError:
                    warn_list = [warnings]
            else:
                warn_list = list(warnings)
            err = item.get("error_message") or ""
            tags = " ".join(str(w) for w in warn_list) + " " + err
            if key != "all" and key not in tags:
                continue
            page = int(item["page_number"])
            idx = int(item.get("figure_index", 0))
            row = QListWidgetItem(f"⚠ p{page:04d} fig{idx:02d} — {item.get('status')}")
            row.setData(_USER_ROLE, (page, idx))
            self.list.addItem(row)

    def _on_row(self, row: int) -> None:
        if row < 0:
            return
        item = self.list.item(row)
        data = item.data(_USER_ROLE)
        if isinstance(data, tuple):
            page, idx = data
            self.detail.setText(f"Page {page} Figure {idx}")
            self.figure_selected.emit(page, idx)

    def _open(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        data = item.data(_USER_ROLE)
        if isinstance(data, tuple):
            self.figure_selected.emit(data[0], data[1])


class _CleanerTab(QWidget):
    page_selected = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_row)
        layout.addWidget(self.list)
        self.detail = QLabel("选择 Cleaner 待审页…")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        self._items: list[dict] = []

    def set_items(self, items: list[dict]) -> None:
        self._items = items
        self.list.clear()
        for item in items:
            page = int(item["page_number"])
            row = QListWidgetItem(
                f"⚠ p{page:04d} — {item.get('decision') or item.get('status')}"
            )
            row.setData(_USER_ROLE, page)
            self.list.addItem(row)

    def _on_row(self, row: int) -> None:
        if row < 0:
            return
        item = self.list.item(row)
        page = item.data(_USER_ROLE)
        if isinstance(page, int):
            self.detail.setText(f"Cleaner page {page}")
            self.page_selected.emit(page)


class ReviewQueue(QWidget):
    page_selected = pyqtSignal(int)
    accept_requested = pyqtSignal(int, str)
    skip_requested = pyqtSignal(int)
    retranscribe_requested = pyqtSignal(int)
    figure_selected = pyqtSignal(int, int)
    cleaner_page_selected = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        box = QGroupBox("审核队列")
        layout = QVBoxLayout(self)
        layout.addWidget(box)
        inner = QVBoxLayout(box)

        self.tabs = QTabWidget()
        self.transcription_tab = _TranscriptionTab()
        self.figures_tab = _FiguresTab()
        self.cleaner_tab = _CleanerTab()
        self.tabs.addTab(self.transcription_tab, "Transcription")
        self.tabs.addTab(self.figures_tab, "Figures")
        self.tabs.addTab(self.cleaner_tab, "Cleaner")
        inner.addWidget(self.tabs)

        self.transcription_tab.page_selected.connect(self.page_selected.emit)
        self.transcription_tab.accept_requested.connect(self.accept_requested.emit)
        self.transcription_tab.skip_requested.connect(self.skip_requested.emit)
        self.transcription_tab.retranscribe_requested.connect(
            self.retranscribe_requested.emit
        )
        self.figures_tab.figure_selected.connect(self.figure_selected.emit)
        self.cleaner_tab.page_selected.connect(self.cleaner_page_selected.emit)

    def set_transcription_items(self, items: list[dict]) -> None:
        self.transcription_tab.set_items(items)

    def set_figure_items(self, items: list[dict]) -> None:
        self.figures_tab.set_items(items)

    def set_cleaner_items(self, items: list[dict]) -> None:
        self.cleaner_tab.set_items(items)

    def show_page_detail(
        self,
        page_number: int,
        issues: str,
        markdown: str,
        attempts: list[tuple[str, Path]],
    ) -> None:
        self.transcription_tab.show_page_detail(page_number, issues, markdown, attempts)

    def current_page(self) -> int | None:
        return self.transcription_tab.current_page()

    # backward compat
    def set_items(self, items: list[dict]) -> None:
        self.set_transcription_items(items)
