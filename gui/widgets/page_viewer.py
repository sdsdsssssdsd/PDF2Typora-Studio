"""Page PNG preview with zoom controls."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class PageViewer(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pages_dir: Path | None = None
        self._page_count = 0
        self._current = 1
        self._zoom = 1.0
        self._fit = True
        self._pixmap: QPixmap | None = None

        root = QVBoxLayout(self)

        nav = QHBoxLayout()
        self.prev_btn = QPushButton("上一页")
        self.next_btn = QPushButton("下一页")
        self.fit_btn = QPushButton("适合窗口")
        self.zoom100_btn = QPushButton("100%")
        self.zoom_in_btn = QPushButton("放大")
        self.zoom_out_btn = QPushButton("缩小")
        self.page_label = QLabel("Page — / —")
        for b in (
            self.prev_btn,
            self.next_btn,
            self.fit_btn,
            self.zoom100_btn,
            self.zoom_in_btn,
            self.zoom_out_btn,
        ):
            nav.addWidget(b)
        nav.addStretch()
        nav.addWidget(self.page_label)
        root.addLayout(nav)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.image_label.setText("尚未导入项目")
        self.scroll.setWidget(self.image_label)
        root.addWidget(self.scroll)

        self.prev_btn.clicked.connect(self.prev_page)
        self.next_btn.clicked.connect(self.next_page)
        self.fit_btn.clicked.connect(self.fit_window)
        self.zoom100_btn.clicked.connect(self.zoom_100)
        self.zoom_in_btn.clicked.connect(lambda: self.adjust_zoom(1.25))
        self.zoom_out_btn.clicked.connect(lambda: self.adjust_zoom(0.8))

    def set_project(self, pages_dir: Path, page_count: int) -> None:
        self._pages_dir = pages_dir
        self._page_count = page_count
        self._current = 1
        self.show_page(1)

    def show_page(self, page_number: int) -> None:
        if self._page_count < 1:
            return
        self._current = max(1, min(page_number, self._page_count))
        self.page_label.setText(f"Page {self._current} / {self._page_count}")
        path = None
        if self._pages_dir is not None:
            path = self._pages_dir / f"page_{self._current:04d}.png"
        if path is None or not path.exists():
            self._pixmap = None
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText("该页面尚未渲染")
            return
        pm = QPixmap(str(path))
        if pm.isNull():
            self._pixmap = None
            self.image_label.setText("无法打开页面图片")
            return
        self._pixmap = pm
        self._apply_zoom()

    def prev_page(self) -> None:
        self.show_page(self._current - 1)

    def next_page(self) -> None:
        self.show_page(self._current + 1)

    def fit_window(self) -> None:
        self._fit = True
        self._apply_zoom()

    def zoom_100(self) -> None:
        self._fit = False
        self._zoom = 1.0
        self._apply_zoom()

    def adjust_zoom(self, factor: float) -> None:
        self._fit = False
        self._zoom = max(0.1, min(8.0, self._zoom * factor))
        self._apply_zoom()

    def _apply_zoom(self) -> None:
        if self._pixmap is None:
            return
        if self._fit:
            viewport = self.scroll.viewport().size()
            scaled = self._pixmap.scaled(
                viewport,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.image_label.setPixmap(scaled)
        else:
            w = max(1, int(self._pixmap.width() * self._zoom))
            h = max(1, int(self._pixmap.height() * self._zoom))
            scaled = self._pixmap.scaled(
                w,
                h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.image_label.setPixmap(scaled)
        self.image_label.setText("")
        self.image_label.adjustSize()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._fit and self._pixmap is not None:
            self._apply_zoom()
