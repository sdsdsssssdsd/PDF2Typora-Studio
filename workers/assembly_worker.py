"""Background Markdown assemble worker."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from config.config_manager import load_config
from core.assemble_models import AssemblyRequest
from services.markdown_assembler import MarkdownAssembler
from utils.logger import get_logger

logger = get_logger("assembly_worker")


class AssemblySignals(QObject):
    started = pyqtSignal()
    progress = pyqtSignal(str)
    completed = pyqtSignal(object)
    cancelled = pyqtSignal()
    error = pyqtSignal(str)


class AssemblyWorker(QRunnable):
    def __init__(
        self,
        *,
        project_root: Path,
        db_path: Path,
        page_numbers: list[int] | None = None,
        force: bool = False,
        allow_unresolved_figures: bool = False,
    ) -> None:
        super().__init__()
        self.project_root = project_root
        self.db_path = db_path
        self.page_numbers = page_numbers
        self.force = force
        self.allow_unresolved_figures = allow_unresolved_figures
        self.signals = AssemblySignals()
        self._cancel = threading.Event()
        self.setAutoDelete(True)

    def request_cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        self.signals.started.emit()
        try:
            cfg = load_config()
            assembler = MarkdownAssembler(
                project_root=self.project_root,
                db_path=self.db_path,
                config=cfg,
            )
            pages = self.page_numbers
            if pages is None:
                pages = assembler._all_pages()
            request = AssemblyRequest(
                project_root=self.project_root,
                page_numbers=tuple(pages),
                preserve_page_markers=bool(
                    (cfg.get("assemble") or {}).get("preserve_page_markers", True)
                ),
                apply_continuity_patches=True,
                allow_unresolved_figures=self.allow_unresolved_figures
                or bool((cfg.get("assemble") or {}).get("allow_unresolved_figures", False)),
                force=self.force,
            )
            result = assembler.assemble(
                request,
                cancel_check=self._cancel.is_set,
                on_progress=lambda m: self.signals.progress.emit(m),
            )
            if result.error == "cancelled":
                self.signals.cancelled.emit()
            elif result.success:
                self.signals.completed.emit(result)
            else:
                self.signals.error.emit(result.error or "assemble failed")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Assembly worker failed")
            self.signals.error.emit(str(exc))
