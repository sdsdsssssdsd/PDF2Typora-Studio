"""GPU / Ollama inference mutex — one Vision request at a time."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

_LOCK = threading.Lock()


def is_inference_busy() -> bool:
    return _LOCK.locked()


def try_acquire_inference_lock() -> bool:
    return _LOCK.acquire(blocking=False)


def acquire_inference_lock(timeout: float | None = None) -> bool:
    if timeout is None:
        return _LOCK.acquire()
    return _LOCK.acquire(timeout=timeout)


def release_inference_lock() -> None:
    if _LOCK.locked():
        _LOCK.release()


@contextmanager
def inference_lock() -> Iterator[None]:
    _LOCK.acquire()
    try:
        yield
    finally:
        if _LOCK.locked():
            _LOCK.release()
