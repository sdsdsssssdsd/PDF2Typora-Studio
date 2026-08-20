"""Assemble readiness + generate raw.md panel."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class AssemblePanel(QWidget):
    check_continuity_requested = pyqtSignal()
    assemble_requested = pyqtSignal()
    open_continuity_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        box = QGroupBox("Markdown Assemble（拼装）")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(box)
        inner = QVBoxLayout(box)

        self.readiness = QLabel("就绪状态: —")
        self.readiness.setWordWrap(True)
        inner.addWidget(self.readiness)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setMinimumHeight(140)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        host = QWidget()
        host_l = QVBoxLayout(host)
        host_l.setContentsMargins(0, 0, 4, 0)

        self.detail = QLabel(
            "转录: —\n插图: —\n页面源: —\n页数: —\n连续性候选: —"
        )
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        host_l.addWidget(self.detail)

        self.blocking = QPlainTextEdit()
        self.blocking.setReadOnly(True)
        self.blocking.setPlaceholderText("阻塞原因会显示在这里…")
        self.blocking.setMaximumBlockCount(200)
        self.blocking.setStyleSheet(
            "QPlainTextEdit { color: #8b3a2a; background: #fff6ef; "
            "border: 1px solid #e0c4a8; border-radius: 6px; padding: 6px; }"
        )
        self.blocking.setMinimumHeight(72)
        host_l.addWidget(self.blocking)
        host_l.addStretch()
        scroll.setWidget(host)
        inner.addWidget(scroll, stretch=1)

        self.hint = QLabel(
            "若插图仍待审：请到「待审队列 / Figure Review」处理，"
            "或勾选下方 Override 强制拼装（未裁好的图可能缺失）。"
        )
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #4a5751;")
        inner.addWidget(self.hint)

        self.allow_unresolved = QCheckBox(
            "允许未解决 Figure（高级 Override，跳过插图待审直接拼装）"
        )
        inner.addWidget(self.allow_unresolved)

        self.output = QLabel("输出: intermediate/raw.md")
        inner.addWidget(self.output)

        btn = QHBoxLayout()
        self.continuity_btn = QPushButton("检查连续性")
        self.open_cont_btn = QPushButton("打开 Continuity Review")
        self.assemble_btn = QPushButton("生成 raw.md")
        self.assemble_btn.setEnabled(False)
        self.continuity_btn.clicked.connect(self.check_continuity_requested.emit)
        self.open_cont_btn.clicked.connect(self.open_continuity_requested.emit)
        self.assemble_btn.clicked.connect(self.assemble_requested.emit)
        btn.addWidget(self.continuity_btn)
        btn.addWidget(self.open_cont_btn)
        btn.addWidget(self.assemble_btn)
        btn.addStretch()
        inner.addLayout(btn)

        self.status = QLabel("Status: —")
        inner.addWidget(self.status)

        self._last_ready = False
        self.allow_unresolved.toggled.connect(self._sync_assemble_enabled)

    def _sync_assemble_enabled(self, _checked: bool = False) -> None:
        self.assemble_btn.setEnabled(
            self._last_ready or self.allow_unresolved.isChecked()
        )

    def update_readiness(self, summary: dict, continuity_candidates: int = 0) -> None:
        ready = bool(summary.get("ready"))
        self._last_ready = ready
        self.readiness.setText(
            "就绪状态: 可以拼装" if ready else "就绪状态: 尚不可拼装"
        )
        fig = summary.get("figure_summary") or {}
        remaining = fig.get("remaining_reviews")
        fig_line = "✓" if summary.get("figures_ready") else "✗"
        if remaining:
            fig_line += f"（待审 {remaining}）"
        self.detail.setText(
            f"转录: {'✓' if summary.get('transcription_ready') else '✗'}\n"
            f"插图: {fig_line}\n"
            f"页面源: {'✓' if summary.get('sources_complete') else '✗'}\n"
            f"页数: {summary.get('pages', 0)}\n"
            f"连续性候选: {continuity_candidates}"
        )
        blocking = list(summary.get("blocking") or [])
        # 把 source_errors 也摊开，方便滚动查看
        for e in summary.get("source_errors") or []:
            if e not in blocking:
                blocking.append(e)
        self.blocking.setPlainText("\n".join(blocking) if blocking else "（无阻塞项）")
        self._sync_assemble_enabled()
        self.status.setText("Status: READY" if ready else "Status: NOT READY")

    def set_running(self, running: bool) -> None:
        if running:
            self.assemble_btn.setEnabled(False)
        else:
            self._sync_assemble_enabled()
        self.continuity_btn.setEnabled(not running)

    def set_status_message(self, msg: str) -> None:
        self.status.setText(f"Status: {msg}")
