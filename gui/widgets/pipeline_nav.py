"""Left-rail pipeline navigation — one clear conversion path."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

STEPS: list[tuple[str, str, str]] = [
    ("import", "1 · 导入 PDF", "选择或拖入要转换的 PDF"),
    ("render", "2 · 渲染页面", "把 PDF 页变成高清图片"),
    ("transcribe", "3 · AI 转录", "Hybrid：OCR+文本API；或 Vision 看图（旧）"),
    ("figures", "4 · 处理插图", "裁剪并嵌入 figures"),
    ("assemble", "5 · 拼装文档", "合并为 intermediate/raw.md"),
    ("clean", "6 · 清洗 Markdown", "去掉 PAGE 标记、规范格式"),
    ("final", "7 · 导出 Typora", "生成 final.md 并导出项目"),
    ("benchmark", "实验 · 引擎对比", "Phase 9.5.2：MinerU/Marker/Docling/Chandra Benchmark"),
]


class PipelineNav(QWidget):
    step_selected = pyqtSignal(str)
    run_current_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PipelineRail")
        self._current = "import"
        self._buttons: dict[str, QPushButton] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(4)

        title = QLabel("转换流程")
        title.setStyleSheet("font-size: 14px; font-weight: 700; color: #1c2421;")
        layout.addWidget(title)

        hint = QLabel("按顺序完成。下方是当前步骤操作区。")
        hint.setObjectName("PipelineHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        for key, label, tip in STEPS:
            btn = QPushButton(label)
            btn.setObjectName("PipelineStep")
            btn.setToolTip(tip)
            btn.setProperty("active", "false")
            btn.setMinimumHeight(32)
            btn.clicked.connect(lambda _=False, k=key: self.select(k))
            self._buttons[key] = btn
            layout.addWidget(btn)

        layout.addSpacing(4)
        self.cta = QPushButton("执行当前步骤")
        self.cta.setObjectName("PrimaryCta")
        self.cta.setToolTip("运行左侧高亮步骤的主操作")
        self.cta.setMinimumHeight(40)
        self.cta.clicked.connect(self._emit_run)
        layout.addWidget(self.cta)

        self.status = QLabel("下一步：导入 PDF")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #4a5751; font-size: 12px; padding-top: 8px;")
        layout.addWidget(self.status)
        # 不再 addStretch：留给下方阶段面板足够高度，避免操作区被挤没

        self.select("import")

    def select(self, key: str) -> None:
        if key not in self._buttons:
            return
        self._current = key
        for k, btn in self._buttons.items():
            active = k == key
            btn.setProperty("active", "true" if active else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        labels = {k: lab for k, lab, _ in STEPS}
        self.status.setText(f"当前：{labels.get(key, key)}")
        tips = {k: tip for k, _, tip in STEPS}
        self.cta.setText(f"执行 · {labels.get(key, key).split('·', 1)[-1].strip()}")
        self.cta.setToolTip(tips.get(key, ""))
        self.step_selected.emit(key)

    def current_step(self) -> str:
        return self._current

    def set_cta_enabled(self, enabled: bool) -> None:
        self.cta.setEnabled(enabled)

    def _emit_run(self) -> None:
        self.run_current_requested.emit(self._current)
