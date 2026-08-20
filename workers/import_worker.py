"""Background PDF import / project creation worker."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from services.project_service import ProjectService
from utils.logger import get_logger

logger = get_logger("import_worker")


class ImportWorkerSignals(QObject):
    progress = pyqtSignal(int, str)  # percent, message
    info_ready = pyqtSignal(object)  # PDFInfo
    completed = pyqtSignal(object)  # Project
    error = pyqtSignal(str)


class ImportWorker(QRunnable):
    def __init__(
        self,
        pdf_path: Path,
        workspace_root: Path | None = None,
    ) -> None:
        super().__init__()
        self.pdf_path = Path(pdf_path)
        self.workspace_root = workspace_root
        self.signals = ImportWorkerSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            service = ProjectService(workspace_root=self.workspace_root)
            self.signals.progress.emit(5, "正在检查 PDF…")
            pdf_info = service.inspect_pdf(self.pdf_path)
            self.signals.info_ready.emit(pdf_info)

            self.signals.progress.emit(25, "正在复制 PDF 并创建项目…")
            project = service.create_project(self.pdf_path)

            self.signals.progress.emit(100, "导入完成")
            self.signals.completed.emit(project)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Import failed")
            self.signals.error.emit(str(exc))
