"""Minimal Vision model benchmark dialog."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ai.base.vision_provider import VisionResult
from ai.providers.ollama_provider import OllamaVisionProvider
from config.config_manager import project_root
from utils.gpu_lock import is_inference_busy
from utils.logger import get_logger
from workers.ollama_worker import make_benchmark_worker

logger = get_logger("benchmark_dialog")


class BenchmarkDialog(QDialog):
    def __init__(
        self,
        provider: OllamaVisionProvider,
        model: str,
        base_url: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("模型测试 (Benchmark)")
        self.setMinimumSize(560, 480)
        self.provider = provider
        self.pool = QThreadPool.globalInstance()

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.server_label = QLabel(base_url)
        self.model_label = QLabel(model)
        form.addRow("Server:", self.server_label)
        form.addRow("Model:", self.model_label)

        img_row = QHBoxLayout()
        default_img = project_root() / "tests" / "assets" / "sample_page.png"
        self.image_edit = QLineEdit(str(default_img) if default_img.exists() else "")
        btn_img = QPushButton("选择图片")
        btn_img.clicked.connect(self._pick_image)
        img_row.addWidget(self.image_edit)
        img_row.addWidget(btn_img)
        form.addRow("Test Image:", img_row)

        self.prompt_edit = QPlainTextEdit("请简要描述图片中的主要内容。")
        self.prompt_edit.setMaximumHeight(80)
        form.addRow("Prompt:", self.prompt_edit)
        layout.addLayout(form)

        self.btn_run = QPushButton("开始测试")
        self.btn_run.clicked.connect(self._run)
        layout.addWidget(self.btn_run)

        self.status_label = QLabel("状态：等待")
        self.timing_label = QLabel("总耗时：—")
        self.length_label = QLabel("输出长度：—")
        layout.addWidget(self.status_label)
        layout.addWidget(self.timing_label)
        layout.addWidget(self.length_label)

        layout.addWidget(QLabel("Result:"))
        self.result_edit = QPlainTextEdit()
        self.result_edit.setReadOnly(True)
        layout.addWidget(self.result_edit)

    def _pick_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择测试图片",
            "",
            "Images (*.png *.jpg *.jpeg *.webp);;All (*.*)",
        )
        if path:
            self.image_edit.setText(path)

    def _run(self) -> None:
        if is_inference_busy():
            self.status_label.setText(
                "状态：AI engine is currently busy with Batch Transcription."
            )
            return
        image = Path(self.image_edit.text().strip())
        prompt = self.prompt_edit.toPlainText().strip()
        model = self.model_label.text().strip()
        if not image.exists():
            self.status_label.setText("状态：图片不存在")
            return
        if not prompt:
            self.status_label.setText("状态：Prompt 为空")
            return

        self.btn_run.setEnabled(False)
        self.status_label.setText("状态：运行中…")
        self.result_edit.clear()
        worker = make_benchmark_worker(self.provider, image, prompt, model)

        def on_ok(result: object) -> None:
            self.btn_run.setEnabled(True)
            if not isinstance(result, VisionResult):
                self.status_label.setText("状态：未知结果类型")
                return
            if result.success:
                self.status_label.setText("状态：成功")
            else:
                self.status_label.setText(f"状态：失败 — {result.error}")
            parts: list[str] = []
            if result.total_duration_ns:
                parts.append(f"总耗时：{result.total_duration_ns / 1e6:.0f} ms")
            else:
                parts.append("总耗时：—")
            if result.load_duration_ns:
                parts.append(f"load={result.load_duration_ns / 1e6:.0f} ms")
            self.timing_label.setText(" | ".join(parts))
            content = result.content or result.markdown or ""
            self.length_label.setText(
                f"输出长度：{len(content)} | "
                f"prompt_eval={result.prompt_eval_count} eval={result.eval_count}"
            )
            self.result_edit.setPlainText(content or (result.error or ""))
            logger.info(
                "Benchmark model=%s success=%s chars=%s",
                model,
                result.success,
                len(content),
            )

        def on_err(msg: str) -> None:
            self.btn_run.setEnabled(True)
            self.status_label.setText(f"状态：错误 — {msg}")
            self.result_edit.setPlainText(msg)

        worker.signals.finished.connect(on_ok)
        worker.signals.error.connect(on_err)
        self.pool.start(worker)
