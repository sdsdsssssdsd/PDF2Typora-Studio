"""Background batch figure extraction worker."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from config.config_manager import load_config
from services.batch_figure_service import BatchFigureService
from utils.logger import get_logger

logger = get_logger("batch_figure_worker")


class BatchFigureSignals(QObject):
    started = pyqtSignal()
    page_started = pyqtSignal(int)
    page_finished = pyqtSignal(object)
    progress = pyqtSignal(str)
    completed = pyqtSignal(object)
    cancelled = pyqtSignal()
    error = pyqtSignal(str)


class BatchFigureWorker(QRunnable):
    def __init__(
        self,
        *,
        project_root: Path,
        pdf_path: Path,
        db_path: Path,
        pdf_hash: str,
        pages: list[int],
        analyze_only: bool = False,
    ) -> None:
        super().__init__()
        self.project_root = project_root
        self.pdf_path = pdf_path
        self.db_path = db_path
        self.pdf_hash = pdf_hash
        self.pages = pages
        self.analyze_only = analyze_only
        self.signals = BatchFigureSignals()
        self._cancel = threading.Event()
        self.setAutoDelete(True)

    def request_cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        self.signals.started.emit()
        svc = BatchFigureService(
            project_root=self.project_root,
            pdf_path=self.pdf_path,
            db_path=self.db_path,
            pdf_hash=self.pdf_hash,
            config=load_config(),
        )

        def on_page(result: Any) -> None:
            self.signals.page_finished.emit(result)

        try:
            summary = svc.process_pages(
                self.pages,
                analyze_only=self.analyze_only,
                cancel_check=self._cancel.is_set,
                on_page=on_page,
            )
            if summary.get("cancelled"):
                self.signals.cancelled.emit()
            else:
                self.signals.completed.emit(summary)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Batch figure worker failed")
            self.signals.error.emit(str(exc))
