"""Background batch transcription worker (Hybrid OCR+API or Vision)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from config.config_manager import load_config
from services.batch_transcription_service import BatchTranscriptionService
from services.transcription_service import TranscriptionService
from utils.gpu_lock import is_inference_busy
from utils.logger import get_logger

logger = get_logger("batch_transcription_worker")


class BatchTranscriptionSignals(QObject):
    started = pyqtSignal(int)
    page_started = pyqtSignal(int)
    page_finished = pyqtSignal(object)
    progress = pyqtSignal(str)
    paused = pyqtSignal(int)
    cancelled = pyqtSignal(int)
    completed = pyqtSignal(object)
    error = pyqtSignal(str)


class BatchTranscriptionWorker(QRunnable):
    def __init__(
        self,
        *,
        provider: Any,
        project_root: Path,
        db_path: Path,
        run_id: int,
        page_count: int,
        profiles=None,
        page_engine: str | None = None,
    ) -> None:
        super().__init__()
        self.provider = provider
        self.project_root = project_root
        self.db_path = db_path
        self.run_id = run_id
        self.page_count = page_count
        self.profiles = profiles
        self.page_engine = page_engine
        self.signals = BatchTranscriptionSignals()
        self._cancel = threading.Event()
        self._pause = threading.Event()
        self.setAutoDelete(True)

    def request_cancel(self) -> None:
        self._cancel.set()

    def request_pause(self) -> None:
        self._pause.set()

    def request_resume(self) -> None:
        self._pause.clear()

    def run(self) -> None:
        cfg = load_config()
        engine = self.page_engine or str(
            (cfg.get("transcription") or {}).get("page_engine") or "hybrid_ocr_api"
        )
        # Vision 才占本地推理锁；Hybrid 走文本 API，不挡 Ollama
        if engine == "vision_only" and is_inference_busy():
            self.signals.error.emit(
                "AI engine is currently busy with another Vision task."
            )
            return
        trans = TranscriptionService(
            self.provider, self.project_root, self.db_path
        )
        service = BatchTranscriptionService(
            transcription=trans,
            project_root=self.project_root,
            db_path=self.db_path,
            profiles=self.profiles,
            page_count=self.page_count,
            config=cfg,
        )
        if self.page_engine:
            service.page_engine = self.page_engine
        self.signals.started.emit(self.run_id)
        service.mark_run(self.run_id, "RUNNING")
        pause_ms = int(
            (cfg.get("batch_transcription") or {}).get("pause_between_pages_ms", 0)
        )
        mode_label = "Hybrid OCR+API" if engine != "vision_only" else "Vision"
        try:
            while True:
                if self._cancel.is_set():
                    service.cancel_remaining(self.run_id)
                    self.signals.cancelled.emit(self.run_id)
                    return
                if self._pause.is_set():
                    service.mark_run(self.run_id, "PAUSED")
                    self.signals.paused.emit(self.run_id)
                    return
                page = service.next_waiting(self.run_id)
                if page is None:
                    report = service.finalize_run(self.run_id)
                    self.signals.completed.emit(report)
                    return
                self.signals.page_started.emit(page)
                self.signals.progress.emit(f"Batch {mode_label} 转录第 {page} 页…")
                result = service.process_page(
                    self.run_id,
                    page,
                    cancel_check=self._cancel.is_set,
                )
                self.signals.page_finished.emit(result)
                service.refresh_counts(self.run_id)
                if pause_ms:
                    time.sleep(pause_ms / 1000.0)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Batch worker failed")
            service.mark_run(self.run_id, "FAILED")
            self.signals.error.emit(str(exc))
