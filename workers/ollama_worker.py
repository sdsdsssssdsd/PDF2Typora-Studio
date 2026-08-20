"""Background workers for Ollama runtime / API operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from ai.providers.ollama_provider import OllamaVisionProvider
from ai.runtime.ollama_manager import OllamaRuntimeManager, OllamaRuntimeStatus
from utils.logger import get_logger

logger = get_logger("ollama_worker")


class OllamaWorkerSignals(QObject):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)


class OllamaTaskWorker(QRunnable):
    """Run a callable in the thread pool and emit its result."""

    def __init__(self, fn: Callable[[], Any], label: str = "") -> None:
        super().__init__()
        self.fn = fn
        self.label = label
        self.signals = OllamaWorkerSignals()

    def run(self) -> None:
        try:
            if self.label:
                self.signals.progress.emit(self.label)
            result = self.fn()
            self.signals.finished.emit(result)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ollama worker failed: %s", self.label)
            self.signals.error.emit(str(exc))


def make_start_worker(manager: OllamaRuntimeManager) -> OllamaTaskWorker:
    return OllamaTaskWorker(manager.start_bundled, "正在启动 Bundled Ollama…")


def make_stop_worker(manager: OllamaRuntimeManager) -> OllamaTaskWorker:
    return OllamaTaskWorker(manager.stop_bundled, "正在停止 Bundled Ollama…")


def make_detect_worker(manager: OllamaRuntimeManager) -> OllamaTaskWorker:
    def _detect() -> dict[str, Any]:
        status = manager.get_runtime_status()
        health = manager.health_check()
        bundled = manager.inspect_runtime_directory()
        return {
            "status": status,
            "health": health,
            "bundled": bundled,
        }

    return OllamaTaskWorker(_detect, "正在检测 Ollama…")


def make_refresh_models_worker(
    provider: OllamaVisionProvider,
) -> OllamaTaskWorker:
    return OllamaTaskWorker(
        lambda: provider.list_model_infos(fetch_capabilities=True),
        "正在刷新模型列表…",
    )


def make_benchmark_worker(
    provider: OllamaVisionProvider,
    image_path: Path,
    prompt: str,
    model: str,
) -> OllamaTaskWorker:
    def _run() -> Any:
        return provider.analyze_image(image_path, prompt, model=model)

    return OllamaTaskWorker(_run, "正在运行 Vision 测试…")
