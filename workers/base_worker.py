"""Worker base class (Phase 3+)."""

from __future__ import annotations

from PyQt6.QtCore import QRunnable, pyqtSignal, QObject


class WorkerSignals(QObject):
    finished = pyqtSignal()
    error = pyqtSignal(str)
    progress = pyqtSignal(int, str)


class BaseWorker(QRunnable):
    def __init__(self) -> None:
        super().__init__()
        self.signals = WorkerSignals()
