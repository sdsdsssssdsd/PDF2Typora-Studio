"""Figure review widget — manual crop, preview, marker placement (Phase 6.5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QKeySequence,
    QPen,
    QPixmap,
    QShortcut,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)


def _bbox_from_item(rect: QGraphicsRectItem, img_w: int, img_h: int) -> tuple[int, int, int, int]:
    r = rect.rect()
    x0 = int(max(0, min(1000, r.x() / img_w * 1000)))
    y0 = int(max(0, min(1000, r.y() / img_h * 1000)))
    x1 = int(max(0, min(1000, (r.x() + r.width()) / img_w * 1000)))
    y1 = int(max(0, min(1000, (r.y() + r.height()) / img_h * 1000)))
    return (x0, y0, x1, y1)


class _ZoomView(QGraphicsView):
    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)


class FigureReview(QWidget):
    accept_requested = pyqtSignal(int, int, object)  # page, index, bbox_1000
    preview_requested = pyqtSignal(int, int, object, object)  # page, index, bbox, candidate_id
    skip_requested = pyqtSignal(int, int)
    not_figure_requested = pyqtSignal(int, int)
    marker_placement_requested = pyqtSignal(int, int, int, str, str)
    open_figure_requested = pyqtSignal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        box = QGroupBox("Figure Review")
        layout = QVBoxLayout(self)
        layout.addWidget(box)
        inner = QVBoxLayout(box)

        nav = QHBoxLayout()
        self.prev_btn = QPushButton("◀ 上一问题")
        self.next_btn = QPushButton("下一问题 ▶")
        self.page_combo = QComboBox()
        self.fig_combo = QComboBox()
        nav.addWidget(self.prev_btn)
        nav.addWidget(QLabel("页:"))
        nav.addWidget(self.page_combo)
        nav.addWidget(QLabel("Figure:"))
        nav.addWidget(self.fig_combo)
        nav.addWidget(self.next_btn)
        inner.addLayout(nav)

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QVBoxLayout()
        self.scene = QGraphicsScene()
        self.view = _ZoomView(self.scene)
        self.view.setMinimumHeight(240)
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        left_w = QWidget()
        left_w.setLayout(left)
        left.addWidget(self.view)

        self.preview_scene = QGraphicsScene()
        self.preview_view = QGraphicsView(self.preview_scene)
        self.preview_view.setMinimumHeight(120)
        left.addWidget(QLabel("Crop Preview"))
        left.addWidget(self.preview_view)

        split.addWidget(left_w)

        right = QVBoxLayout()
        self.info = QLabel("选择待审 Figure…")
        self.info.setWordWrap(True)
        right.addWidget(self.info)

        self.candidate_list = QListWidget()
        self.candidate_list.setMaximumHeight(120)
        right.addWidget(QLabel("Candidates"))
        right.addWidget(self.candidate_list)

        self.marker_status = QLabel("Marker: —")
        self.marker_status.setWordWrap(True)
        right.addWidget(self.marker_status)

        self.placement_md = QPlainTextEdit()
        self.placement_md.setPlaceholderText("Resolved 工作副本（用于 missing_marker 插入位置）…")
        self.placement_md.setMaximumHeight(100)
        right.addWidget(self.placement_md)

        right_w = QWidget()
        right_w.setLayout(right)
        split.addWidget(right_w)
        inner.addWidget(split)

        btn = QHBoxLayout()
        self.use_cand_btn = QPushButton("使用候选")
        self.manual_btn = QPushButton("手工框选 (R)")
        self.preview_btn = QPushButton("生成预览 (P)")
        self.accept_btn = QPushButton("接受 (A)")
        self.placement_btn = QPushButton("确认插入位置")
        self.not_fig_btn = QPushButton("不是Figure (N)")
        self.skip_btn = QPushButton("跳过")
        for b, slot in (
            (self.use_cand_btn, self._use_candidate),
            (self.manual_btn, self._toggle_manual),
            (self.preview_btn, self._preview),
            (self.accept_btn, self._accept),
            (self.placement_btn, self._confirm_placement),
            (self.not_fig_btn, self._not_figure),
            (self.skip_btn, self._skip),
        ):
            b.clicked.connect(slot)
            btn.addWidget(b)
        inner.addLayout(btn)

        self.prev_btn.clicked.connect(self._prev_item)
        self.next_btn.clicked.connect(self._next_item)
        self.page_combo.currentIndexChanged.connect(self._reload_figure)
        self.fig_combo.currentIndexChanged.connect(self._show_figure)
        self.candidate_list.currentRowChanged.connect(self._on_candidate)

        QShortcut(QKeySequence("A"), self, self._accept)
        QShortcut(QKeySequence("R"), self, self._toggle_manual)
        QShortcut(QKeySequence("P"), self, self._preview)
        QShortcut(QKeySequence("N"), self, self._not_figure)

        self._items: list[dict] = []
        self._candidates: list[dict] = []
        self._pix_item: QGraphicsPixmapItem | None = None
        self._overlay_items: list[QGraphicsRectItem] = []
        self._manual_rect: QGraphicsRectItem | None = None
        self._manual_mode = False
        self._drag_start: tuple[float, float] | None = None
        self._img_size = (1, 1)
        self._pending: tuple[int, int] | None = None
        self._current_bbox: tuple[int, int, int, int] | None = None
        self._selected_candidate_id: str | None = None
        self.view.viewport().installEventFilter(self)

    def set_items(self, items: list[dict]) -> None:
        self._items = items
        pages = sorted({int(i["page_number"]) for i in items})
        self.page_combo.blockSignals(True)
        self.page_combo.clear()
        for p in pages:
            self.page_combo.addItem(f"{p:04d}", p)
        self.page_combo.blockSignals(False)
        if pages:
            self._reload_figure()
        else:
            self.info.setText("无待审 Figure")

    def load_figure_detail(
        self,
        *,
        page_number: int,
        figure_index: int,
        page_image: Path | None,
        info: str,
        marker_issues: list[str],
        candidates: list[dict],
        ai_bbox: tuple[int, int, int, int] | None,
        resolved_md: str = "",
    ) -> None:
        self._candidates = candidates
        self.candidate_list.clear()
        review_threshold = 0.55
        for c in candidates:
            score = float(c.get("score") or 0)
            if score < review_threshold and c.get("type") != "ai":
                continue
            label = (
                f"{c.get('candidate_id')} · {c.get('type')} · "
                f"score={score:.2f} · {c.get('width')}×{c.get('height')}"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, c)
            self.candidate_list.addItem(item)

        self.info.setText(info)
        self.marker_status.setText(f"Marker: {', '.join(marker_issues) or 'OK'}")
        self.placement_md.setPlainText(resolved_md)
        self._show_page_image(page_image, ai_bbox, candidates)
        self._pending = (page_number, figure_index)
        idx = self.fig_combo.findData(figure_index)
        if idx >= 0:
            self.fig_combo.setCurrentIndex(idx)

    def show_preview_image(self, path: Path) -> None:
        self.preview_scene.clear()
        if path.exists():
            pix = QPixmap(str(path))
            self.preview_scene.addPixmap(pix)
            self.preview_scene.setSceneRect(QRectF(pix.rect()))
            self.preview_view.fitInView(
                self.preview_scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio
            )

    def _show_page_image(
        self,
        image_path: Path | None,
        ai_bbox: tuple[int, int, int, int] | None,
        candidates: list[dict],
    ) -> None:
        self.scene.clear()
        self._overlay_items.clear()
        self._manual_rect = None
        if not image_path or not image_path.exists():
            self.info.setText("页面 PNG 不存在")
            return
        pix = QPixmap(str(image_path))
        self._pix_item = self.scene.addPixmap(pix)
        self.scene.setSceneRect(QRectF(pix.rect()))
        self._img_size = (pix.width(), pix.height())
        w, h = self._img_size

        if ai_bbox:
            self._add_overlay(ai_bbox, Qt.GlobalColor.red, "AI")

        for c in candidates:
            bb = c.get("bbox_1000")
            if isinstance(bb, str):
                bb = tuple(json.loads(bb))
            if not bb:
                continue
            color = (
                Qt.GlobalColor.blue
                if c.get("type") == "raster"
                else Qt.GlobalColor.darkGreen
            )
            self._add_overlay(bb, color, str(c.get("type", "")))

        if self._current_bbox:
            self._set_manual_rect(self._current_bbox)

    def _add_overlay(
        self, bbox: tuple[int, int, int, int], color: Qt.GlobalColor, _tag: str
    ) -> None:
        if not self._pix_item:
            return
        w, h = self._img_size
        x0, y0, x1, y1 = bbox
        rect = QGraphicsRectItem(x0 / 1000 * w, y0 / 1000 * h, (x1 - x0) / 1000 * w, (y1 - y0) / 1000 * h)
        pen = QPen(color)
        pen.setWidth(2)
        rect.setPen(pen)
        self.scene.addItem(rect)
        self._overlay_items.append(rect)

    def _set_manual_rect(self, bbox: tuple[int, int, int, int]) -> None:
        if self._manual_rect:
            self.scene.removeItem(self._manual_rect)
        w, h = self._img_size
        x0, y0, x1, y1 = bbox
        self._manual_rect = QGraphicsRectItem(
            x0 / 1000 * w, y0 / 1000 * h, (x1 - x0) / 1000 * w, (y1 - y0) / 1000 * h
        )
        pen = QPen(Qt.GlobalColor.magenta)
        pen.setWidth(3)
        pen.setStyle(Qt.PenStyle.DashLine)
        self._manual_rect.setPen(pen)
        self.scene.addItem(self._manual_rect)
        self._current_bbox = bbox

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.view.viewport() and self._manual_mode:
            et = event.type()
            if et == event.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                pos = self.view.mapToScene(event.position().toPoint())
                self._drag_start = (pos.x(), pos.y())
                return True
            if et == event.Type.MouseMove and self._drag_start and event.buttons() & Qt.MouseButton.LeftButton:
                pos = self.view.mapToScene(event.position().toPoint())
                x0, y0 = self._drag_start
                x1, y1 = pos.x(), pos.y()
                bx0, bx1 = sorted((x0, x1))
                by0, by1 = sorted((y0, y1))
                w, h = self._img_size
                bbox = (
                    int(bx0 / w * 1000),
                    int(by0 / h * 1000),
                    int(bx1 / w * 1000),
                    int(by1 / h * 1000),
                )
                self._set_manual_rect(bbox)
                return True
            if et == event.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self._drag_start = None
                return True
        return super().eventFilter(obj, event)

    def _reload_figure(self) -> None:
        page = self.page_combo.currentData()
        if page is None:
            return
        figs = [i for i in self._items if int(i["page_number"]) == int(page)]
        self.fig_combo.clear()
        for f in figs:
            idx = int(f.get("figure_index", 0))
            self.fig_combo.addItem(f"fig {idx:02d} — {f.get('status')}", idx)
        self._show_figure()

    def _show_figure(self) -> None:
        page = self.page_combo.currentData()
        idx = self.fig_combo.currentData()
        if page is None or idx is None:
            return
        self.open_figure_requested.emit(int(page), int(idx))

    def _on_candidate(self, row: int) -> None:
        if row < 0:
            return
        item = self.candidate_list.item(row)
        c = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(c, dict):
            return
        bb = c.get("bbox_1000")
        if isinstance(bb, str):
            bb = tuple(json.loads(bb))
        if bb:
            self._selected_candidate_id = c.get("candidate_id")
            self._set_manual_rect(tuple(bb))

    def _use_candidate(self) -> None:
        self._on_candidate(self.candidate_list.currentRow())

    def _toggle_manual(self) -> None:
        self._manual_mode = not self._manual_mode
        self.manual_btn.setText("框选中…" if self._manual_mode else "手工框选 (R)")
        self.view.setDragMode(
            QGraphicsView.DragMode.NoDrag
            if self._manual_mode
            else QGraphicsView.DragMode.ScrollHandDrag
        )

    def _current_figure(self) -> tuple[int, int] | None:
        page = self.page_combo.currentData()
        idx = self.fig_combo.currentData()
        if page is None or idx is None:
            return None
        return int(page), int(idx)

    def _preview(self) -> None:
        fig = self._current_figure()
        if fig is None or not self._current_bbox:
            return
        p, i = fig
        self.preview_requested.emit(p, i, self._current_bbox, self._selected_candidate_id)

    def _accept(self) -> None:
        fig = self._current_figure()
        if fig is None or not self._current_bbox:
            QMessageBox.warning(self, "接受", "请先框选或选择候选区域")
            return
        p, i = fig
        self.accept_requested.emit(p, i, self._current_bbox)

    def _confirm_placement(self) -> None:
        fig = self._current_figure()
        if fig is None:
            return
        p, i = fig
        cursor = self.placement_md.textCursor()
        offset = cursor.position()
        text = self.placement_md.toPlainText()
        before = text[max(0, offset - 40) : offset]
        after = text[offset : offset + 40]
        self.marker_placement_requested.emit(p, i, offset, before, after)

    def _not_figure(self) -> None:
        fig = self._current_figure()
        if fig is None:
            return
        p, i = fig
        if (
            QMessageBox.question(self, "确认", "确定标记为「不是 Figure」？")
            == QMessageBox.StandardButton.Yes
        ):
            self.not_figure_requested.emit(p, i)

    def _skip(self) -> None:
        fig = self._current_figure()
        if fig is None:
            return
        self.skip_requested.emit(*fig)

    def _item_index(self) -> int:
        page = self.page_combo.currentData()
        idx = self.fig_combo.currentData()
        for i, row in enumerate(self._items):
            if int(row["page_number"]) == int(page) and int(row["figure_index"]) == int(idx):
                return i
        return -1

    def _prev_item(self) -> None:
        cur = self._item_index()
        if cur <= 0:
            return
        row = self._items[cur - 1]
        self._select_item(int(row["page_number"]), int(row["figure_index"]))

    def _next_item(self) -> None:
        cur = self._item_index()
        if cur < 0 or cur >= len(self._items) - 1:
            return
        row = self._items[cur + 1]
        self._select_item(int(row["page_number"]), int(row["figure_index"]))

    def _select_item(self, page: int, fig_idx: int) -> None:
        pi = self.page_combo.findData(page)
        if pi >= 0:
            self.page_combo.setCurrentIndex(pi)
        fi = self.fig_combo.findData(fig_idx)
        if fi >= 0:
            self.fig_combo.setCurrentIndex(fi)

    def advance_after_action(self) -> None:
        self._next_item()
