"""Phase 9.5.1 page evidence models — PDF native + OCR → reconstruction."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

EVIDENCE_PIPELINE_VERSION = "1"


class PageTextSourceMode(str, Enum):
    PDF_NATIVE = "PDF_NATIVE"
    PDF_NATIVE_PLUS_OCR = "PDF_NATIVE_PLUS_OCR"
    OCR_PRIMARY = "OCR_PRIMARY"
    OCR_ONLY = "OCR_ONLY"


class PageEngineMode(str, Enum):
    VISION_ONLY = "vision_only"
    HYBRID_OCR_API = "hybrid_ocr_api"
    PDF_OCR_LOCAL = "pdf_ocr_local"
    PARSER_ONLY = "parser_only"


@dataclass
class EvidenceBlock:
    id: str
    type: str  # heading | paragraph | table | figure_group | caption | ocr_line | formula
    text: str = ""
    bbox: list[float] | None = None
    bold: bool = False
    italic: bool = False
    color: str = "#000000"
    source: str = "pdf"  # pdf | ocr | layout | figure
    confidence: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class PageEvidenceManifest:
    page_number: int
    mode: PageTextSourceMode = PageTextSourceMode.PDF_NATIVE
    pdf_hash: str = ""
    pipeline_version: str = EVIDENCE_PIPELINE_VERSION
    blocks: list[EvidenceBlock] = field(default_factory=list)
    pdf_plain_text: str = ""
    ocr_plain_text: str = ""
    figure_labels: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page_number,
            "mode": self.mode.value,
            "pdf_hash": self.pdf_hash,
            "pipeline_version": self.pipeline_version,
            "blocks": [b.to_dict() for b in self.blocks],
            "figure_labels": list(self.figure_labels),
            "warnings": list(self.warnings),
            "meta": dict(self.meta),
            "pdf_char_count": len(self.pdf_plain_text),
            "ocr_char_count": len(self.ocr_plain_text),
        }

    def reconstruction_payload(self) -> dict[str, Any]:
        """Compact payload for DeepSeek text API (no raw dumps)."""
        return {
            "page": self.page_number,
            "mode": self.mode.value,
            "blocks": [
                {
                    "id": b.id,
                    "type": b.type,
                    "text": b.text,
                    "bbox": b.bbox,
                    "bold": b.bold,
                    "color": b.color,
                    "source": b.source,
                    **(
                        {"label": b.extra.get("label")}
                        if b.extra.get("label")
                        else {}
                    ),
                    **(
                        {"subfigures": b.extra.get("subfigures")}
                        if b.extra.get("subfigures")
                        else {}
                    ),
                }
                for b in self.blocks
            ],
            "figure_labels": list(self.figure_labels),
            "warnings": list(self.warnings),
        }
