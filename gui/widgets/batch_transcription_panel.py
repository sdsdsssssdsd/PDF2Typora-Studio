"""Batch page-transcription control panel (Hybrid OCR+API / Vision)."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from core.models import ModelQualification
from gui.widgets.combo_utils import configure_model_combo

_ENGINE_HELP = {
    "hybrid_ocr_api": (
        "推荐：本地 PDF 文字层 + OCR（看字）→ 文本 API 重建 Markdown。"
        "不必选 Vision 模型；下方选 DeepSeek 等文本模型即可。"
    ),
    "vision_only": (
        "旧路径：整页图片交给 Vision 模型（Ollama/多模态 API）直接抄写。"
        "需要本机或云端 Vision 模型。"
    ),
    "pdf_ocr_local": (
        "本地：PDF 文字 + OCR，重建用本地 LLM（仍走证据管线）。"
    ),
    "parser_only": (
        "仅文档解析器输出（可选依赖）；不跑 Vision。"
    ),
}


class BatchTranscriptionPanel(QWidget):
    start_requested = pyqtSignal()
    pause_requested = pyqtSignal()
    resume_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    qualify_requested = pyqtSignal()
    refresh_models_requested = pyqtSignal()
    open_api_settings_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._has_qualified = False
        box = QGroupBox("页面转录（批量）")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(box)
        inner = QVBoxLayout(box)

        self.demo_hint = QLabel(
            "流程示意：PDF/页面图 →【本地 OCR + PDF 文字】→ 证据 JSON →【文本 API 重建】→ Markdown\n"
            "Hybrid 模式看字靠 OCR，不靠 Vision；Vision Only 才需要多模态看图模型。"
        )
        self.demo_hint.setWordWrap(True)
        self.demo_hint.setStyleSheet(
            "background:#fff6ef; border:1px solid #e0c4a8; border-radius:8px; "
            "padding:8px; color:#1c2421;"
        )
        inner.addWidget(self.demo_hint)

        self.engine_label = QLabel("当前：Hybrid（OCR + 文本 API）")
        self.engine_label.setStyleSheet("color: #4a5751;")
        inner.addWidget(self.engine_label)

        form = QFormLayout()
        self.engine_combo = QComboBox()
        self.engine_combo.addItem(
            "Hybrid：本地 OCR + PDF文字 → 文本API重建（推荐）", "hybrid_ocr_api"
        )
        self.engine_combo.addItem(
            "Vision Only：多模态看图抄写（旧版对比）", "vision_only"
        )
        self.engine_combo.addItem(
            "PDF+OCR+本地LLM：本地证据 + 本地重建", "pdf_ocr_local"
        )
        self.engine_combo.addItem(
            "Document Parser Only：仅解析器", "parser_only"
        )
        form.addRow("页面转录方式:", self.engine_combo)

        self.engine_help = QLabel(_ENGINE_HELP["hybrid_ocr_api"])
        self.engine_help.setWordWrap(True)
        self.engine_help.setStyleSheet("color:#4a5751; font-size:12px;")
        form.addRow("", self.engine_help)

        self.model_row_label = QLabel("文本重建模型:")
        self.primary_combo = QComboBox()
        self.primary_combo.setMinimumWidth(200)
        configure_model_combo(self.primary_combo, max_visible=16)
        self.primary_combo.setEnabled(True)
        form.addRow(self.model_row_label, self.primary_combo)

        self.fallback_combo = QComboBox()
        self.fallback_combo.addItem("None", None)
        configure_model_combo(self.fallback_combo, max_visible=12)
        self.fallback_combo.setEnabled(True)
        self.fallback_row_label = QLabel("Vision Fallback:")
        form.addRow(self.fallback_row_label, self.fallback_combo)

        self.radio_all = QRadioButton("全部已渲染且未转录")
        self.radio_custom = QRadioButton("自定义")
        self.radio_all.setChecked(True)
        self.page_group = QButtonGroup(self)
        self.page_group.addButton(self.radio_all)
        self.page_group.addButton(self.radio_custom)
        self.range_edit = QLineEdit()
        self.range_edit.setPlaceholderText("例如 1-8 或 1,4,8")
        self.range_edit.setEnabled(False)
        self.radio_custom.toggled.connect(self.range_edit.setEnabled)
        page_col = QVBoxLayout()
        page_col.addWidget(self.radio_all)
        page_col.addWidget(self.radio_custom)
        page_col.addWidget(self.range_edit)
        form.addRow("页面:", page_col)

        self.auto_accept = QCheckBox("自动验收")
        self.auto_accept.setChecked(True)
        form.addRow("", self.auto_accept)
        self.skip_qualify = QCheckBox("跳过 3 页资格测试（直接开始）")
        self.skip_qualify.setChecked(True)
        self.skip_qualify.setToolTip(
            "Hybrid / OCR+API 不需要 Vision 资格测试。"
            "Vision Only 时也可勾选跳过（不推荐，可能 schema 不稳定）。"
        )
        form.addRow("", self.skip_qualify)
        inner.addLayout(form)

        btn = QHBoxLayout()
        self.refresh_models_btn = QPushButton("刷新模型列表")
        self.api_btn = QPushButton("外部 API…")
        self.start_btn = QPushButton("开始批量转录")
        self.start_btn.setObjectName("PrimaryCta")
        self.qualify_btn = QPushButton("3 页资格测试")
        self.qualify_btn.setToolTip(
            "可选：对 Vision 模型做 Phase 5A 三页抽测。"
            "Hybrid 文本 API 路径不需要此项。"
        )
        self.pause_btn = QPushButton("暂停")
        self.resume_btn = QPushButton("继续")
        self.cancel_btn = QPushButton("取消")
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.refresh_models_btn.clicked.connect(self.refresh_models_requested.emit)
        self.api_btn.clicked.connect(self.open_api_settings_requested.emit)
        self.start_btn.clicked.connect(self.start_requested.emit)
        self.qualify_btn.clicked.connect(self.qualify_requested.emit)
        self.pause_btn.clicked.connect(self.pause_requested.emit)
        self.resume_btn.clicked.connect(self.resume_requested.emit)
        self.cancel_btn.clicked.connect(self.cancel_requested.emit)
        self.skip_qualify.toggled.connect(self._on_skip_qualify_toggled)
        for b in (
            self.refresh_models_btn,
            self.api_btn,
            self.start_btn,
            self.qualify_btn,
            self.pause_btn,
            self.resume_btn,
            self.cancel_btn,
        ):
            btn.addWidget(b)
        btn.addStretch()
        inner.addLayout(btn)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        inner.addWidget(self.progress)
        self.status_label = QLabel(
            "Hybrid：配置文本 API（如 DeepSeek）后即可开始；不必选 Vision 模型。"
        )
        inner.addWidget(self.status_label)
        self.stats_label = QLabel("自动验收 0 · 待审 0 · 失败 0 · 缓存 0")
        inner.addWidget(self.stats_label)

        self.engine_combo.currentIndexChanged.connect(self._refresh_engine_copy)
        self._refresh_engine_copy()

    def _refresh_engine_copy(self, _index: int = 0) -> None:
        mode = self.selected_engine()
        self.engine_help.setText(_ENGINE_HELP.get(mode, ""))
        vision = mode == "vision_only"
        if vision:
            self.model_row_label.setText("Vision 看图模型:")
            self.fallback_row_label.setText("Vision Fallback:")
            self.fallback_combo.setEnabled(True)
            self.primary_combo.setToolTip("多模态 Vision 模型（看整页图）")
            # Vision：默认仍建议资格测试，但允许勾选跳过
            self.skip_qualify.setChecked(False)
            self.skip_qualify.setEnabled(True)
            self.qualify_btn.setEnabled(True)
            self.qualify_btn.setVisible(True)
        else:
            self.model_row_label.setText("文本重建模型（非 Vision）:")
            self.fallback_row_label.setText("Fallback（Hybrid 通常不需）:")
            self.fallback_combo.setEnabled(False)
            self.primary_combo.setToolTip(
                "文本 API 模型，用于把 OCR/PDF 证据重建成 Markdown；不是 Vision 看图模型"
            )
            # Hybrid：资格测试不适用，默认跳过；按钮仍可见便于切回 Vision 时习惯一致
            self.skip_qualify.setChecked(True)
            self.skip_qualify.setEnabled(False)
            self.qualify_btn.setEnabled(False)
            self.qualify_btn.setVisible(True)
        self._sync_start_enabled()

    def _on_skip_qualify_toggled(self, _checked: bool = False) -> None:
        self._sync_start_enabled()

    def skip_qualification(self) -> bool:
        """True when user may start without Phase 5A QUALIFIED."""
        if self.selected_engine() != "vision_only":
            return True
        return bool(self.skip_qualify.isChecked())

    def _sync_start_enabled(self) -> None:
        has_models = self.primary_combo.count() > 0
        if not has_models:
            self.start_btn.setEnabled(False)
            return
        if self.selected_engine() != "vision_only":
            self.start_btn.setEnabled(True)
            return
        ok = bool(getattr(self, "_has_qualified", False)) or self.skip_qualification()
        self.start_btn.setEnabled(ok)

    def set_models(
        self,
        items: list[tuple[str, str, str]],
        *,
        has_qualified: bool,
        source_tag: str = "",
    ) -> None:
        """items: (name, digest, qualification) — display uses source_tag prefix when set."""
        self.primary_combo.clear()
        self.fallback_combo.clear()
        self.fallback_combo.addItem("None", None)
        prefix = f"[{source_tag}] " if source_tag else ""
        for name, digest, qual in items:
            mark = {
                ModelQualification.QUALIFIED.value: "✓",
                ModelQualification.LIMITED.value: "⚠",
                ModelQualification.DISABLED.value: "✗",
                ModelQualification.UNTESTED.value: "○",
            }.get(qual, "○")
            label = f"{prefix}{name}  {mark} {qual}"
            self.primary_combo.addItem(label, name)
            if qual == ModelQualification.QUALIFIED.value:
                self.fallback_combo.addItem(label, name)
        self._has_qualified = has_qualified
        self.primary_combo.setEnabled(True)
        self.fallback_combo.setEnabled(self.selected_engine() == "vision_only")
        hybrid = self.selected_engine() != "vision_only"
        self._sync_start_enabled()
        if self.primary_combo.count() == 0:
            if hybrid:
                self.status_label.setText(
                    "没有文本重建模型：请在「外部 API」配置对应服务并刷新。"
                )
            else:
                self.status_label.setText(
                    "没有可用 Vision 模型：请启动 Ollama 后点「刷新模型列表」。"
                )
        elif hybrid and source_tag:
            self.status_label.setText(
                f"已加载 {self.primary_combo.count()} 个【{source_tag}】文本重建模型"
                "（无需 3 页资格测试，可直接开始）。"
            )
        elif hybrid:
            self.status_label.setText(
                "Hybrid 就绪：无需资格测试，确认文本 API 模型后即可开始。"
            )
        elif not has_qualified and not self.skip_qualification():
            self.status_label.setText(
                "Vision：可先跑「3 页资格测试」，或勾选「跳过」后直接开始。"
            )
        else:
            self.status_label.setText("可以开始批量转录。")

    def set_engine_label(self, text: str) -> None:
        self.engine_label.setText(text)

    def selected_engine(self) -> str:
        return str(self.engine_combo.currentData() or "hybrid_ocr_api")

    def set_engine(self, mode: str) -> None:
        idx = self.engine_combo.findData(mode)
        if idx >= 0:
            self.engine_combo.setCurrentIndex(idx)
        self._refresh_engine_copy()

    def selected_primary(self) -> str | None:
        data = self.primary_combo.currentData()
        return str(data) if data else None

    def selected_fallback(self) -> str | None:
        data = self.fallback_combo.currentData()
        return str(data) if data else None

    def page_range_expression(self) -> str | None:
        if self.radio_all.isChecked():
            return None
        return self.range_edit.text().strip() or None

    def set_running(self, running: bool) -> None:
        if running:
            self.start_btn.setEnabled(False)
        else:
            self._sync_start_enabled()
        self.pause_btn.setEnabled(running)
        self.cancel_btn.setEnabled(running)
        self.resume_btn.setEnabled(False)

    def set_paused(self, paused: bool) -> None:
        self.start_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(paused)
        self.cancel_btn.setEnabled(paused)

    def update_progress(
        self,
        done: int,
        total: int,
        *,
        auto: int = 0,
        review: int = 0,
        failed: int = 0,
        cached: int = 0,
        current: int | None = None,
        eta: str = "",
    ) -> None:
        if total:
            self.progress.setValue(int(100 * done / total))
        msg = f"{done} / {total}"
        if current is not None:
            msg += f"  当前第 {current} 页"
        if eta:
            msg += f"  剩余约 {eta}"
        self.status_label.setText(msg)
        self.stats_label.setText(
            f"自动验收 {auto} · 待审 {review} · 失败 {failed} · 缓存 {cached}"
        )
