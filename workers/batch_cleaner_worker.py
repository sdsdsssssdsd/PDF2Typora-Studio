"""Background batch cleaner worker."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from config.config_manager import load_config
from services.batch_cleaner_service import BatchCleanerService
from utils.logger import get_logger

logger = get_logger("batch_cleaner_worker")


class BatchCleanerSignals(QObject):
    started = pyqtSignal()
    progress = pyqtSignal(str)
    page_finished = pyqtSignal(object)
    completed = pyqtSignal(object)
    cancelled = pyqtSignal()
    error = pyqtSignal(str)


class BatchCleanerWorker(QRunnable):
    def __init__(
        self,
        *,
        project_root: Path,
        db_path: Path,
        pages: list[int] | None = None,
        force: bool = False,
        text_provider: Any | None = None,
    ) -> None:
        super().__init__()
        self.project_root = project_root
        self.db_path = db_path
        self.pages = pages
        self.force = force
        self.text_provider = text_provider
        self.signals = BatchCleanerSignals()
        self._cancel = threading.Event()
        self._pause = threading.Event()
        self.setAutoDelete(True)

    def request_cancel(self) -> None:
        self._cancel.set()
        self._pause.clear()

    def request_pause(self) -> None:
        self._pause.set()

    def request_resume(self) -> None:
        self._pause.clear()

    def run(self) -> None:
        self.signals.started.emit()
        try:
            cfg = load_config()
            mode = getattr(self, "_desired_mode", None)
            if mode:
                cleaner = dict(cfg.get("cleaner") or {})
                cleaner["mode"] = mode
                cfg = dict(cfg)
                cfg["cleaner"] = cleaner
            svc = BatchCleanerService(
                project_root=self.project_root,
                db_path=self.db_path,
                config=cfg,
                text_provider=self.text_provider,
            )
            summary = svc.process_pages(
                self.pages,
                force=self.force,
                cancel_check=self._cancel.is_set,
                pause_check=self._pause.is_set,
                on_page=lambda r: self.signals.page_finished.emit(r),
                on_progress=lambda m: self.signals.progress.emit(m),
            )
            if summary.get("cancelled"):
                self.signals.cancelled.emit()
            else:
                self.signals.completed.emit(summary)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Batch cleaner failed")
            self.signals.error.emit(str(exc))
