"""Background PDF render worker."""

from __future__ import annotations

import threading
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from core.models import PageRenderResult, RenderRequest
from services.render_service import RenderService
from utils.logger import get_logger

logger = get_logger("render_worker")


class RenderWorkerSignals(QObject):
    started = pyqtSignal()
    page_started = pyqtSignal(int)
    page_finished = pyqtSignal(object)  # PageRenderResult
    progress_changed = pyqtSignal(int, int, str)  # done, total, message
    status_changed = pyqtSignal(str)
    error = pyqtSignal(str)
    cancelled = pyqtSignal(object)  # summary dict
    completed = pyqtSignal(object)  # summary dict


class RenderWorker(QRunnable):
    def __init__(self, request: RenderRequest) -> None:
        super().__init__()
        self.request = request
        self.signals = RenderWorkerSignals()
        self._cancel = threading.Event()
        self.setAutoDelete(True)

    def request_cancel(self) -> None:
        self._cancel.set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:
        service = RenderService()
        total = len(self.request.pages)
        done = 0
        results: list[PageRenderResult] = []
        self.signals.started.emit()
        self.signals.status_changed.emit("rendering")

        def on_start(page_number: int) -> None:
            self.signals.page_started.emit(page_number)
            self.signals.progress_changed.emit(
                done,
                total,
                f"正在渲染第 {page_number} / {self.request.pages[-1] if self.request.pages else 0} 页",
            )

        def on_done(result: PageRenderResult) -> None:
            nonlocal done
            done += 1
            results.append(result)
            name = (
                result.image_path.name
                if result.image_path
                else f"page_{result.page_number:04d}"
            )
            if result.cached:
                msg = f"缓存命中 {name} ({done}/{total})"
            elif result.success:
                msg = f"已完成 {name} ({done}/{total})"
            elif result.cancelled:
                msg = f"已取消 ({done}/{total})"
            else:
                msg = f"失败 第{result.page_number}页 ({done}/{total})"
            self.signals.page_finished.emit(result)
            self.signals.progress_changed.emit(done, total, msg)

        try:
            service.render_pages(
                self.request,
                cancel_check=self._cancel.is_set,
                on_page_start=on_start,
                on_page_done=on_done,
            )
        except Exception as exc:
            logger.exception("Render job failed")
            self.signals.error.emit(str(exc))
            self.signals.status_changed.emit("error")
            return

        summary = _summarize(results, total)
        if self._cancel.is_set():
            self.signals.status_changed.emit("cancelled")
            self.signals.cancelled.emit(summary)
        else:
            self.signals.status_changed.emit("completed")
            self.signals.completed.emit(summary)


def _summarize(results: list[PageRenderResult], total: int) -> dict[str, Any]:
    success = sum(1 for r in results if r.success and not r.cached)
    cached = sum(1 for r in results if r.cached)
    failed = sum(1 for r in results if not r.success and not r.cancelled)
    cancelled = sum(1 for r in results if r.cancelled)
    return {
        "total": total,
        "processed": len(results),
        "success": success,
        "cached": cached,
        "failed": failed,
        "cancelled": cancelled,
        "results": results,
    }
