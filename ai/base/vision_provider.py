"""Vision provider abstract interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ModelCapabilities:
    name: str
    supports_vision: bool = False
    supports_tools: bool = False
    context_length: int | None = None
    extra: dict[str, object] = field(default_factory=dict)


@dataclass
class VisionResult:
    """Result of a vision analysis call.

    Phase 1 used ``markdown`` / ``error``. Phase 2 adds success flags and
    timing metrics while keeping backward-compatible fields.
    """

    markdown: str = ""
    content: str = ""
    raw_response: str = ""
    model: str = ""
    provider: str = ""
    figures: list[dict[str, object]] = field(default_factory=list)
    needs_review: bool = False
    success: bool = True
    error: str | None = None
    total_duration_ns: int | None = None
    load_duration_ns: int | None = None
    prompt_eval_count: int | None = None
    eval_count: int | None = None

    def __post_init__(self) -> None:
        if self.content and not self.markdown:
            self.markdown = self.content
        elif self.markdown and not self.content:
            self.content = self.markdown
        if self.error:
            self.success = False


class VisionProvider(ABC):
    @abstractmethod
    def health_check(self) -> bool:
        ...

    @abstractmethod
    def list_models(self) -> list[str]:
        ...

    @abstractmethod
    def get_model_capabilities(self, model: str) -> ModelCapabilities:
        ...

    @abstractmethod
    def analyze_page(
        self,
        image_path: Path,
        prompt: str,
        context: str | None = None,
    ) -> VisionResult:
        ...
