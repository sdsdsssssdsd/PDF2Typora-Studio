"""Text provider abstract interface (Phase 7+)."""

from __future__ import annotations

from abc import ABC, abstractmethod


class TextProvider(ABC):
    @abstractmethod
    def health_check(self) -> bool:
        ...

    @abstractmethod
    def complete(self, prompt: str, text: str) -> str:
        ...
