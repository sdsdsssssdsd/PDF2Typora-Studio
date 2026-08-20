"""Continuity review widget — manual boundary patches (Phase 7)."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from core.assemble_models import ContinuityPatchAction


class ContinuityReview(QWidget):
    save_requested = pyqtSignal(int, int, str, str)  # left, right, action, custom
    next_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        box = QGroupBox("Continuity Review")
        layout = QVBoxLayout(self)
        layout.addWidget(box)
        inner = QVBoxLayout(box)

        top = QHBoxLayout()
        self.pair_combo = QComboBox()
        top.addWidget(QLabel("边界:"))
        top.addWidget(self.pair_combo, stretch=1)
        inner.addLayout(top)

        self.title = QLabel("未检测到连续性候选")
        inner.addWidget(self.title)

        self.left_tail = QPlainTextEdit()
        self.left_tail.setReadOnly(True)
        self.left_tail.setMaximumHeight(90)
        self.right_head = QPlainTextEdit()
        self.right_head.setReadOnly(True)
        self.right_head.setMaximumHeight(90)
        inner.addWidget(QLabel("上一页尾部"))
        inner.addWidget(self.left_tail)
        inner.addWidget(QLabel("下一页头部"))
        inner.addWidget(self.right_head)

        self.signals_label = QLabel("Signals: —")
        self.signals_label.setWordWrap(True)
        inner.addWidget(self.signals_label)

        self.action_group = QButtonGroup(self)
        self.radio_no = QRadioButton("No action")
        self.radio_space = QRadioButton("Join with space")
        self.radio_nospace = QRadioButton("Join without space")
        self.radio_newline = QRadioButton("Join with newline")
        self.radio_custom = QRadioButton("Custom")
        self.radio_no.setChecked(True)
        for i, r in enumerate(
            (
                self.radio_no,
                self.radio_space,
                self.radio_nospace,
                self.radio_newline,
                self.radio_custom,
            )
        ):
            self.action_group.addButton(r, i)
            inner.addWidget(r)

        self.custom = QPlainTextEdit()
        self.custom.setPlaceholderText("Custom boundary replacement…")
        self.custom.setMaximumHeight(60)
        inner.addWidget(self.custom)

        btn = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.next_btn = QPushButton("Next")
        self.save_btn.clicked.connect(self._save)
        self.next_btn.clicked.connect(self.next_requested.emit)
        btn.addWidget(self.save_btn)
        btn.addWidget(self.next_btn)
        btn.addStretch()
        inner.addLayout(btn)

        self._items: list[dict] = []
        self.pair_combo.currentIndexChanged.connect(self._show_current)

    def set_candidates(self, items: list[dict]) -> None:
        self._items = items
        self.pair_combo.blockSignals(True)
        self.pair_combo.clear()
        for it in items:
            left = int(it["left_page"])
            right = int(it["right_page"])
            self.pair_combo.addItem(f"Page {left} → {right}", (left, right))
        self.pair_combo.blockSignals(False)
        if items:
            self._show_current()
        else:
            self.title.setText("未检测到连续性候选")
            self.left_tail.clear()
            self.right_head.clear()
            self.signals_label.setText("Signals: —")

    def _show_current(self) -> None:
        idx = self.pair_combo.currentIndex()
        if idx < 0 or idx >= len(self._items):
            return
        it = self._items[idx]
        left = int(it["left_page"])
        right = int(it["right_page"])
        self.title.setText(f"Continuity Review: Page {left} → {right}")
        self.left_tail.setPlainText(it.get("left_tail") or "")
        self.right_head.setPlainText(it.get("right_head") or "")
        flags = it.get("source_flags") or []
        score = it.get("suspicion_score", 0)
        self.signals_label.setText(
            f"Signals: {', '.join(flags) or '—'} · score={score}"
        )
        action = it.get("action") or ContinuityPatchAction.NO_ACTION.value
        mapping = {
            ContinuityPatchAction.NO_ACTION.value: self.radio_no,
            ContinuityPatchAction.JOIN_WITH_SPACE.value: self.radio_space,
            ContinuityPatchAction.JOIN_WITHOUT_SPACE.value: self.radio_nospace,
            ContinuityPatchAction.JOIN_WITH_NEWLINE.value: self.radio_newline,
            ContinuityPatchAction.CUSTOM_REPLACEMENT.value: self.radio_custom,
        }
        (mapping.get(action) or self.radio_no).setChecked(True)
        self.custom.setPlainText(it.get("custom_text") or "")

    def _current_action(self) -> str:
        if self.radio_space.isChecked():
            return ContinuityPatchAction.JOIN_WITH_SPACE.value
        if self.radio_nospace.isChecked():
            return ContinuityPatchAction.JOIN_WITHOUT_SPACE.value
        if self.radio_newline.isChecked():
            return ContinuityPatchAction.JOIN_WITH_NEWLINE.value
        if self.radio_custom.isChecked():
            return ContinuityPatchAction.CUSTOM_REPLACEMENT.value
        return ContinuityPatchAction.NO_ACTION.value

    def _save(self) -> None:
        data = self.pair_combo.currentData()
        if not data:
            return
        left, right = data
        self.save_requested.emit(
            int(left),
            int(right),
            self._current_action(),
            self.custom.toPlainText(),
        )

    def select_next(self) -> None:
        i = self.pair_combo.currentIndex()
        if i < self.pair_combo.count() - 1:
            self.pair_combo.setCurrentIndex(i + 1)
