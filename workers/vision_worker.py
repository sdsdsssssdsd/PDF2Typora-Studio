"""Background Vision transcription worker."""

from __future__ import annotations

import threading
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from ai.providers.ollama_provider import OllamaVisionProvider
from core.models import TranscriptionOptions
from services.transcription_service import TranscriptionAttempt, TranscriptionService
from utils.logger import get_logger

logger = get_logger("vision_worker")


class VisionWorkerSignals(QObject):
    started = pyqtSignal()
    progress = pyqtSignal(str)
    finished = pyqtSignal(object)  # TranscriptionAttempt
    error = pyqtSignal(str)
    cancelled = pyqtSignal()


class VisionWorker(QRunnable):
    def __init__(
        self,
        *,
        provider: OllamaVisionProvider,
        project_root: Path,
        db_path: Path,
        page_number: int,
        image_path: Path,
        model: str,
        options: TranscriptionOptions | None = None,
    ) -> None:
        super().__init__()
        self.provider = provider
        self.project_root = project_root
        self.db_path = db_path
        self.page_number = page_number
        self.image_path = image_path
        self.model = model
        self.options = options or TranscriptionOptions()
        self.signals = VisionWorkerSignals()
        self._cancel = threading.Event()
        self.setAutoDelete(True)

    def request_cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        self.signals.started.emit()
        self.signals.progress.emit(f"正在转录第 {self.page_number} 页…")
        service = TranscriptionService(
            self.provider, self.project_root, self.db_path
        )
        try:
            attempt = service.transcribe_page(
                page_number=self.page_number,
                image_path=self.image_path,
                model=self.model,
                options=self.options,
                cancel_check=self._cancel.is_set,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Vision worker failed")
            self.signals.error.emit(str(exc))
            return

        if attempt.status == "CANCELLED":
            self.signals.cancelled.emit()
            return
        self.signals.finished.emit(attempt)


class VisionCompareWorker(QRunnable):
    """Sequentially run multiple models on one page (no concurrency)."""

    def __init__(
        self,
        *,
        provider: OllamaVisionProvider,
        project_root: Path,
        db_path: Path,
        page_number: int,
        image_path: Path,
        models: list[str],
        options: TranscriptionOptions | None = None,
    ) -> None:
        super().__init__()
        self.provider = provider
        self.project_root = project_root
        self.db_path = db_path
        self.page_number = page_number
        self.image_path = image_path
        self.models = models
        self.options = options or TranscriptionOptions(keep_alive=0)
        self.signals = VisionWorkerSignals()
        self._cancel = threading.Event()
        self.setAutoDelete(True)
        self.results: list[TranscriptionAttempt] = []

    def request_cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        self.signals.started.emit()
        service = TranscriptionService(
            self.provider, self.project_root, self.db_path
        )
        for model in self.models:
            if self._cancel.is_set():
                self.signals.cancelled.emit()
                return
            self.signals.progress.emit(f"比较模型: {model}")
            opts = TranscriptionOptions(
                temperature=self.options.temperature,
                num_ctx=self.options.num_ctx,
                think=self.options.think,
                keep_alive=0,
                schema_retry_attempts=self.options.schema_retry_attempts,
                use_cache=self.options.use_cache,
                force=self.options.force,
            )
            try:
                attempt = service.transcribe_page(
                    page_number=self.page_number,
                    image_path=self.image_path,
                    model=model,
                    options=opts,
                    cancel_check=self._cancel.is_set,
                )
                self.results.append(attempt)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Compare failed for %s", model)
                self.results.append(
                    TranscriptionAttempt(
                        attempt_dir=Path("."),
                        request_hash="",
                        model=model,
                        model_digest="",
                        status="FAILED",
                        error=str(exc),
                        error_code="UNKNOWN_ERROR",
                    )
                )
        self.signals.finished.emit(self.results)
