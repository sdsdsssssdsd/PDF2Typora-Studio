"""Single-page Vision transcription workbench panel."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThreadPool, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ai.providers.ollama_provider import OllamaVisionProvider
from core.models import StageStatus, TranscriptionOptions
from core.project import Project
from services.transcription_service import TranscriptionAttempt, TranscriptionService
from storage.database import Database
from storage.repository import ProjectRepository
from gui.widgets.combo_utils import configure_model_combo
from utils.logger import get_logger
from utils.gpu_lock import is_inference_busy
from workers.vision_worker import VisionCompareWorker, VisionWorker

logger = get_logger("transcription_panel")


class TranscriptionPanel(QWidget):
    accepted = pyqtSignal(int)  # page_number
    open_api_settings_requested = pyqtSignal()
    refresh_models_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project: Project | None = None
        self._page_number = 1
        self._provider: OllamaVisionProvider | None = None
        self._base_url = "http://127.0.0.1:11434"
        self._attempt: TranscriptionAttempt | None = None
        self._worker: VisionWorker | VisionCompareWorker | None = None
        self._pool = QThreadPool.globalInstance()
        self._original_markdown = ""
        self._page_engine = "hybrid_ocr_api"

        box = QGroupBox("页面转录（单页）")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(box)
        inner = QVBoxLayout(box)

        self.demo_hint = QLabel(
            "Hybrid：本地 OCR + PDF 文字层 → 证据 → 文本 API 重建（不必 Vision）\n"
            "Vision Only：整页图交给多模态模型抄写（需要 Vision 模型）"
        )
        self.demo_hint.setWordWrap(True)
        self.demo_hint.setStyleSheet(
            "background:#fff6ef; border:1px solid #e0c4a8; border-radius:8px; "
            "padding:8px; color:#1c2421;"
        )
        inner.addWidget(self.demo_hint)

        self.engine_label = QLabel("当前方式：Hybrid（OCR + 文本 API）")
        self.engine_label.setStyleSheet("color: #4a5751;")
        inner.addWidget(self.engine_label)

        form = QFormLayout()
        self.model_row_label = QLabel("文本重建模型:")
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(280)
        configure_model_combo(self.model_combo, max_visible=16)
        self.model_combo.setEnabled(True)
        form.addRow(self.model_row_label, self.model_combo)

        self.ctx_combo = QComboBox()
        self.ctx_combo.addItem("Auto", None)
        self.ctx_combo.addItem("4096", 4096)
        self.ctx_combo.addItem("8192", 8192)
        self.ctx_combo.setEnabled(True)
        form.addRow("Context（Vision 用）:", self.ctx_combo)

        self.force_chk = QCheckBox("强制重新运行（忽略缓存）")
        form.addRow("", self.force_chk)
        inner.addLayout(form)

        btn_row = QHBoxLayout()
        self.refresh_btn = QPushButton("刷新模型列表")
        self.api_btn = QPushButton("外部 API…")
        self.run_btn = QPushButton("转录当前页")
        self.cancel_btn = QPushButton("取消")
        self.compare_btn = QPushButton("比较 Vision 模型")
        self.accept_btn = QPushButton("接受此结果")
        self.cancel_btn.setEnabled(False)
        self.accept_btn.setEnabled(False)
        self.run_btn.setEnabled(False)
        for b in (
            self.refresh_btn,
            self.api_btn,
            self.run_btn,
            self.cancel_btn,
            self.compare_btn,
            self.accept_btn,
        ):
            btn_row.addWidget(b)
        inner.addLayout(btn_row)

        self.status_label = QLabel("状态：等待")
        self.meta_label = QLabel("")
        self.meta_label.setWordWrap(True)
        inner.addWidget(self.status_label)
        inner.addWidget(self.meta_label)

        inner.addWidget(QLabel("Markdown 预览（可编辑后接受）:"))
        self.md_edit = QPlainTextEdit()
        self.md_edit.setPlaceholderText("转录结果将显示在这里")
        inner.addWidget(self.md_edit)

        inner.addWidget(QLabel("Warnings:"))
        self.warn_edit = QPlainTextEdit()
        self.warn_edit.setReadOnly(True)
        self.warn_edit.setMaximumHeight(90)
        inner.addWidget(self.warn_edit)

        self.refresh_btn.clicked.connect(self.refresh_models_requested.emit)
        self.api_btn.clicked.connect(self.open_api_settings_requested.emit)
        self.run_btn.clicked.connect(self._run_once)
        self.cancel_btn.clicked.connect(self._cancel)
        self.compare_btn.clicked.connect(self._run_compare)
        self.accept_btn.clicked.connect(self._accept)

    def set_engine_label(self, text: str) -> None:
        self.engine_label.setText(text)

    def set_page_engine(self, mode: str) -> None:
        self._page_engine = mode or "hybrid_ocr_api"
        vision = self._page_engine == "vision_only"
        if vision:
            self.model_row_label.setText("Vision 看图模型:")
            self.model_combo.setToolTip("多模态模型：直接看页面图抄写")
            self.compare_btn.setVisible(True)
            self.ctx_combo.setEnabled(True)
            self.demo_hint.setText(
                "Vision Only：整页图 → Vision 模型抄写 Markdown（需要 Vision 模型）"
            )
        else:
            self.model_row_label.setText("文本重建模型（非 Vision）:")
            self.model_combo.setToolTip(
                "文本 API（如 DeepSeek）：把本地 OCR/PDF 证据重建成 Markdown，不是看图模型"
            )
            self.compare_btn.setVisible(False)
            self.ctx_combo.setEnabled(False)
            self.demo_hint.setText(
                "Hybrid：页面图/PDF →【本地 OCR + PDF 文字】→ 证据 JSON →【文本 API 重建】→ Markdown\n"
                "看字靠 OCR，不必选 Vision；下方选文本模型（或先配外部 API）。"
            )
        self._update_enabled()

    def set_provider(self, provider) -> None:
        """Inject Ollama or OpenAI-compatible provider."""
        self._provider = provider
        base = getattr(provider, "base_url", None)
        if base:
            self._base_url = str(base).rstrip("/")

    def set_ollama_base_url(self, url: str) -> None:
        self._base_url = url.rstrip("/")
        # Keep existing non-Ollama provider if active
        from ai.providers.openai_compatible_provider import OpenAICompatibleProvider

        if isinstance(self._provider, OpenAICompatibleProvider):
            return
        self._provider = OllamaVisionProvider(self._base_url)

    def set_project(self, project: Project | None) -> None:
        self._project = project
        self._update_enabled()

    def set_page(self, page_number: int) -> None:
        self._page_number = page_number
        self._attempt = None
        self.accept_btn.setEnabled(False)
        self.md_edit.clear()
        self.warn_edit.clear()
        self.status_label.setText(f"状态：当前页 {page_number}")
        self._update_enabled()

    def _provider_or_create(self):
        if self._provider is None:
            self._provider = OllamaVisionProvider(self._base_url)
        return self._provider

    def set_model_choices(
        self,
        items: list[tuple[str, str]],
        *,
        status: str = "",
        select: str | None = None,
    ) -> None:
        """items: (display_label, model_id)."""
        previous = select or self.model_combo.currentData()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for label, mid in items:
            self.model_combo.addItem(label, mid)
        if previous:
            idx = self.model_combo.findData(previous)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
        self.model_combo.blockSignals(False)
        if status:
            self.status_label.setText(status)
        self.model_combo.setEnabled(True)
        self._update_enabled()

    def refresh_models(self) -> None:
        # Prefer main-window orchestrated refresh (API vs local)
        self.refresh_models_requested.emit()

    def _page_rendered(self) -> bool:
        project = self._project
        if project is None:
            return False
        png = project.pages_dir / f"page_{self._page_number:04d}.png"
        if not png.exists():
            return False
        db = Database(project.db_path)
        try:
            db.initialize()
            repo = ProjectRepository(db)
            state = repo.get_stage_state(self._page_number, "render")
            if state is None:
                return True
            return state.get("status") in (
                StageStatus.SUCCESS.value,
                StageStatus.CACHED.value,
            )
        finally:
            db.close()

    def _update_enabled(self) -> None:
        hybrid = self._page_engine != "vision_only"
        has_model = bool(self.model_combo.currentData())
        # Hybrid: can run with deterministic fallback even without model list
        ready = (
            self._project is not None
            and self._page_rendered()
            and self._worker is None
            and (hybrid or has_model)
        )
        self.run_btn.setEnabled(ready)
        self.compare_btn.setEnabled((not hybrid) and ready and self.model_combo.count() > 1)
        self.cancel_btn.setEnabled(self._worker is not None)

    def _options(self) -> TranscriptionOptions:
        ctx = self.ctx_combo.currentData()
        return TranscriptionOptions(
            temperature=0.0,
            num_ctx=ctx,
            think=False,
            keep_alive="5m",
            schema_retry_attempts=1,
            use_cache=not self.force_chk.isChecked(),
            force=self.force_chk.isChecked(),
        )

    def _run_once(self) -> None:
        if is_inference_busy():
            QMessageBox.information(
                self,
                "AI 忙碌",
                "AI engine is currently busy with Batch Transcription.",
            )
            return
        project = self._project
        if project is None:
            return

        if self._page_engine != "vision_only":
            self._run_hybrid(project)
            return

        model = self.model_combo.currentData()
        if not model:
            QMessageBox.information(self, "转录", "请先选择 Vision 模型。")
            return
        image = project.pages_dir / f"page_{self._page_number:04d}.png"
        worker = VisionWorker(
            provider=self._provider_or_create(),
            project_root=project.root,
            db_path=project.db_path,
            page_number=self._page_number,
            image_path=image,
            model=str(model),
            options=self._options(),
        )
        self._worker = worker
        self._update_enabled()
        self.status_label.setText("状态：Vision 运行中…")

        def on_ok(attempt: object) -> None:
            self._worker = None
            self._update_enabled()
            if isinstance(attempt, TranscriptionAttempt):
                self._show_attempt(attempt)

        def on_err(msg: str) -> None:
            self._worker = None
            self._update_enabled()
            self.status_label.setText(f"状态：失败 — {msg}")

        worker.signals.finished.connect(on_ok)
        worker.signals.error.connect(on_err)
        self._pool.start(worker)

    def _run_hybrid(self, project: Project) -> None:
        from services.hybrid_transcription_service import HybridTranscriptionService

        self.status_label.setText("状态：Hybrid OCR+API 重建中…")
        self.run_btn.setEnabled(False)
        model = self.model_combo.currentData()
        try:
            svc = HybridTranscriptionService(project)
            result = svc.transcribe_page(
                self._page_number,
                run_ocr=True,
                model=str(model) if model else None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("hybrid failed")
            self.status_label.setText(f"状态：Hybrid 失败 — {exc}")
            self._update_enabled()
            QMessageBox.warning(self, "Hybrid 转录失败", str(exc))
            return

        self.md_edit.setPlainText(result.markdown or "")
        self.warn_edit.setPlainText("\n".join(result.warnings))
        self._original_markdown = result.markdown or ""
        self.accept_btn.setEnabled(bool(result.markdown))
        status = "完成" if result.ok else "失败"
        if result.needs_review:
            status += "（需审阅）"
        self.status_label.setText(
            f"状态：Hybrid {status} · evidence={result.evidence_path}"
        )
        self.meta_label.setText(
            f"engine=hybrid_ocr_api · review={result.needs_review} · err={result.error or '—'}"
        )
        self._update_enabled()

    def _run_compare(self) -> None:
        if is_inference_busy():
            QMessageBox.information(
                self,
                "AI 忙碌",
                "AI engine is currently busy with Batch Transcription.",
            )
            return
        project = self._project
        if project is None:
            return
        models = [
            self.model_combo.itemData(i)
            for i in range(self.model_combo.count())
            if self.model_combo.itemData(i)
        ]
        if not models:
            return
        # Prefer up to 3 vision models already listed
        models = [str(m) for m in models[:3]]
        reply = QMessageBox.question(
            self,
            "比较模型",
            "将顺序比较以下模型（不并发，结束后 unload）：\n"
            + "\n".join(models)
            + "\n\n可能较慢，是否继续？",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        image = project.pages_dir / f"page_{self._page_number:04d}.png"
        worker = VisionCompareWorker(
            provider=self._provider_or_create(),
            project_root=project.root,
            db_path=project.db_path,
            page_number=self._page_number,
            image_path=image,
            models=models,
            options=self._options(),
        )
        self._worker = worker
        self._update_enabled()

        def on_ok(results: object) -> None:
            self._worker = None
            self._update_enabled()
            if not isinstance(results, list):
                return
            self._write_comparison_report(results)
            lines = []
            for a in results:
                if not isinstance(a, TranscriptionAttempt):
                    continue
                dur = (a.metrics or {}).get("total_duration_ns")
                sec = f"{dur / 1e9:.1f}s" if dur else "?"
                lines.append(
                    f"{a.model}: {a.status} {sec} warn={len(a.validation_warnings)}"
                )
            self.warn_edit.setPlainText("\n".join(lines))
            self.status_label.setText("状态：比较完成（见 experiments/comparisons）")
            if results and isinstance(results[0], TranscriptionAttempt):
                # show last successful
                for a in reversed(results):
                    if a.status == "SUCCESS" and a.result:
                        self._show_attempt(a)
                        break

        def on_err(msg: str) -> None:
            self._worker = None
            self._update_enabled()
            QMessageBox.warning(self, "比较失败", msg)

        worker.signals.finished.connect(on_ok)
        worker.signals.error.connect(on_err)
        worker.signals.progress.connect(self.status_label.setText)
        self._pool.start(worker)

    def _write_comparison_report(self, results: list) -> None:
        import json
        from datetime import datetime

        project = self._project
        if project is None:
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = (
            project.root
            / "experiments"
            / "comparisons"
            / f"page_{self._page_number:04d}_{stamp}"
        )
        out.mkdir(parents=True, exist_ok=True)
        summary = []
        for a in results:
            if not isinstance(a, TranscriptionAttempt):
                continue
            entry = {
                "model": a.model,
                "model_digest": a.model_digest,
                "status": a.status,
                "error": a.error,
                "warnings": a.validation_warnings,
                "metrics": a.metrics,
                "attempt_dir": str(a.attempt_dir),
                "needs_review": bool(a.result.needs_review) if a.result else None,
                "chars": len(a.result.markdown) if a.result else 0,
            }
            summary.append(entry)
            model_dir = out / re_safe(a.model)
            model_dir.mkdir(parents=True, exist_ok=True)
            (model_dir / "summary.json").write_text(
                json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        (out / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # also project-level report
        report = project.root / "experiments" / "model_comparison_report.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(
                {"page": self._page_number, "results": summary},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _show_attempt(self, attempt: TranscriptionAttempt) -> None:
        self._attempt = attempt
        if attempt.status != "SUCCESS" or attempt.result is None:
            self.status_label.setText(
                f"状态：{attempt.status} — {attempt.error or ''}"
            )
            self.accept_btn.setEnabled(False)
            return
        md = attempt.result.markdown
        self._original_markdown = md
        self.md_edit.setPlainText(md)
        warns = list(attempt.validation_warnings) + list(attempt.result.warnings)
        self.warn_edit.setPlainText("\n".join(warns) if warns else "(无)")
        dur = (attempt.metrics or {}).get("total_duration_ns")
        sec = f"{dur / 1e9:.1f}s" if dur else "?"
        vram = (attempt.metrics or {}).get("size_vram")
        vram_s = f"{vram / (1024**3):.2f} GB" if vram else "?"
        cache = "缓存" if attempt.cached else "新推理"
        self.meta_label.setText(
            f"{cache} | {attempt.model} | {sec} | VRAM={vram_s} | "
            f"review={attempt.result.needs_review}"
        )
        self.status_label.setText("状态：SUCCESS — 可接受或编辑后接受")
        self.accept_btn.setEnabled(True)

    def _accept(self) -> None:
        project = self._project
        if project is None:
            return
        # Hybrid path: write canonical from current editor text
        if self._page_engine != "vision_only" and self._attempt is None:
            from services.hybrid_transcription_service import (
                HybridPageResult,
                HybridTranscriptionService,
            )

            edited = self.md_edit.toPlainText()
            if not edited.strip():
                QMessageBox.warning(self, "接受失败", "没有可接受的 Markdown。")
                return
            model = self.model_combo.currentData()
            svc = HybridTranscriptionService(project)
            try:
                path = svc.accept_canonical(
                    page_number=self._page_number,
                    result=HybridPageResult(
                        page_number=self._page_number,
                        ok=True,
                        markdown=edited,
                        needs_review=False,
                    ),
                    model=str(model) if model else "hybrid",
                    acceptance_mode="manual",
                )
            except Exception as exc:  # noqa: BLE001
                QMessageBox.critical(self, "接受失败", str(exc))
                return
            self.status_label.setText(f"状态：已接受 Hybrid → {path.name}")
            self.accepted.emit(self._page_number)
            return

        if self._attempt is None or self._attempt.result is None:
            return
        edited = self.md_edit.toPlainText()
        manually = edited != self._original_markdown
        service = TranscriptionService(
            self._provider_or_create(), project.root, project.db_path
        )
        try:
            path = service.accept_result(
                page_number=self._page_number,
                attempt=self._attempt,
                markdown_override=edited,
                manually_edited=manually,
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "接受失败", str(exc))
            return
        self.status_label.setText(f"状态：已接受 → {path.name}")
        self.accepted.emit(self._page_number)

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
            self.status_label.setText("状态：取消请求已发送…")


def re_safe(name: str) -> str:
    import re

    return re.sub(r"[^\w.-]+", "_", name)[:60]
