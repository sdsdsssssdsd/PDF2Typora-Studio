"""Phase 9.5.2 unified document page model (Docling-inspired DOM)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


DOCUMENT_PAGE_MODEL_VERSION = "1"


class BlockSource(str, Enum):
    PDF_NATIVE = "PDF_NATIVE"
    LAYOUT_ENGINE = "LAYOUT_ENGINE"
    OCR = "OCR"
    VLM = "VLM"
    MANUAL = "MANUAL"
    PARSER = "PARSER"


class BlockType(str, Enum):
    TEXT = "text"
    HEADING = "heading"
    FORMULA = "formula"
    TABLE = "table"
    FIGURE_GROUP = "figure_group"
    CAPTION = "caption"
    LIST = "list"
    HEADER = "header"
    FOOTER = "footer"
    CODE = "code"
    UNKNOWN = "unknown"


@dataclass
class DocumentBlock:
    block_id: str
    type: str
    text: str = ""
    bbox: list[float] | None = None
    reading_order: int = 0
    style: dict[str, Any] = field(default_factory=dict)
    source: str = BlockSource.PDF_NATIVE.value
    confidence: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DocumentPageEvidence:
    """Unified page evidence from any DocumentParserProvider."""

    page_number: int
    engine: str
    width: float = 0.0
    height: float = 0.0
    blocks: list[DocumentBlock] = field(default_factory=list)
    plain_text: str = ""
    markdown: str = ""
    figure_labels: list[str] = field(default_factory=list)
    table_count: int = 0
    formula_count: int = 0
    warnings: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    model_version: str = DOCUMENT_PAGE_MODEL_VERSION
    ok: bool = True
    error: str | None = None
    duration_ms: float = 0.0
    installed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "engine": self.engine,
            "width": self.width,
            "height": self.height,
            "blocks": [b.to_dict() for b in self.blocks],
            "plain_text": self.plain_text,
            "markdown": self.markdown,
            "figure_labels": list(self.figure_labels),
            "table_count": self.table_count,
            "formula_count": self.formula_count,
            "warnings": list(self.warnings),
            "provenance": dict(self.provenance),
            "model_version": self.model_version,
            "ok": self.ok,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "installed": self.installed,
        }

    def text_char_count(self) -> int:
        if self.plain_text.strip():
            return len(self.plain_text)
        return sum(len(b.text or "") for b in self.blocks)

    def figure_count(self) -> int:
        if self.figure_labels:
            return len(self.figure_labels)
        return sum(1 for b in self.blocks if b.type in {"figure_group", "figure", "image", "chart"})
