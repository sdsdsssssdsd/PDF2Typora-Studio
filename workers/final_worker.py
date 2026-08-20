"""Background Final validation / freeze / export worker."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Literal

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from config.config_manager import load_config
from services.final_freeze_service import FinalFreezeService
from services.typora_export_service import TyporaExportService
from utils.logger import get_logger

logger = get_logger("final_worker")

FinalAction = Literal["validate", "freeze", "export"]


class FinalSignals(QObject):
    started = pyqtSignal()
    progress = pyqtSignal(str)
    completed = pyqtSignal(object)
    error = pyqtSignal(str)


class FinalWorker(QRunnable):
    def __init__(
        self,
        *,
        action: FinalAction,
        project_root: Path,
        db_path: Path,
        export_root: Path | None = None,
        include_source_pdf: bool = True,
        force: bool = False,
    ) -> None:
        super().__init__()
        self.action = action
        self.project_root = project_root
        self.db_path = db_path
        self.export_root = export_root
        self.include_source_pdf = include_source_pdf
        self.force = force
        self.signals = FinalSignals()
        self._cancel = threading.Event()
        self.setAutoDelete(True)

    def request_cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        self.signals.started.emit()
        try:
            cfg = load_config()
            freeze = FinalFreezeService(
                project_root=self.project_root,
                db_path=self.db_path,
                config=cfg,
            )
            if self.action == "validate":
                result = freeze.validate_only()
                self.signals.completed.emit({"action": "validate", "result": result})
                return
            if self.action == "freeze":
                result = freeze.freeze(
                    force=self.force,
                    on_progress=lambda m: self.signals.progress.emit(m),
                )
                self.signals.completed.emit({"action": "freeze", "result": result})
                return
            if self.action == "export":
                exporter = TyporaExportService(
                    project_root=self.project_root,
                    db_path=self.db_path,
                    config=cfg,
                )
                result = exporter.export(
                    export_root=self.export_root,
                    include_source_pdf=self.include_source_pdf,
                    force=self.force,
                    on_progress=lambda m: self.signals.progress.emit(m),
                )
                self.signals.completed.emit({"action": "export", "result": result})
                return
            self.signals.error.emit(f"unknown_action:{self.action}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Final worker failed")
            self.signals.error.emit(str(exc))
