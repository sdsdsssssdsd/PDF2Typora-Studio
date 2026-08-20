"""Simple page list with render status."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from core.models import StageStatus

_USER_ROLE = Qt.ItemDataRole.UserRole


class PageList(QWidget):
    page_selected = pyqtSignal(int)
    rerender_requested = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_row)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.list)
        self._statuses: dict[int, str] = {}

    def set_page_count(self, count: int) -> None:
        self.list.clear()
        self._statuses = {n: StageStatus.WAITING.value for n in range(1, count + 1)}
        for n in range(1, count + 1):
            item = QListWidgetItem(self._label(n, StageStatus.WAITING.value))
            item.setData(_USER_ROLE, n)
            self.list.addItem(item)

    def set_status(self, page_number: int, status: str) -> None:
        self._statuses[page_number] = status
        row = page_number - 1
        if 0 <= row < self.list.count():
            self.list.item(row).setText(self._label(page_number, status))

    def apply_statuses(self, mapping: dict[int, str]) -> None:
        for page, status in mapping.items():
            self.set_status(page, status)

    def select_page(self, page_number: int) -> None:
        row = page_number - 1
        if 0 <= row < self.list.count():
            self.list.setCurrentRow(row)

    def _label(self, page_number: int, status: str) -> str:
        icon = {
            StageStatus.SUCCESS.value: "✓",
            StageStatus.CACHED.value: "✓",
            StageStatus.RUNNING.value: "●",
            StageStatus.FAILED.value: "✗",
            StageStatus.CANCELLED.value: "–",
            StageStatus.WAITING.value: "○",
            StageStatus.NEEDS_REVIEW.value: "⚠",
        }.get(status, "○")
        return f"{page_number:03d}  {icon}"

    def _on_row(self, row: int) -> None:
        if row < 0:
            return
        item = self.list.item(row)
        page = item.data(_USER_ROLE)
        if isinstance(page, int):
            self.page_selected.emit(page)

    def _context_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        page = item.data(_USER_ROLE)
        if not isinstance(page, int):
            return
        menu = QMenu(self)
        act = QAction("重新渲染本页", self)
        act.triggered.connect(lambda: self.rerender_requested.emit(page))
        menu.addAction(act)
        menu.exec(self.list.mapToGlobal(pos))
