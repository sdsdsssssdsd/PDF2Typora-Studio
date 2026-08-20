"""Phase 9.5 layout / figure-group domain models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

LAYOUT_PIPELINE_VERSION = "1"
FIGURE_GROUP_PIPELINE_VERSION = "1"


@dataclass
class TextSpanStyle:
    text: str
    font: str = ""
    size: float = 0.0
    flags: int = 0
    color_int: int = 0
    color_hex: str = "#000000"
    bold: bool = False
    italic: bool = False
    bbox_pdf: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    bbox_1000: tuple[int, int, int, int] = (0, 0, 0, 0)
    block_no: int = 0
    line_no: int = 0
    span_no: int = 0


@dataclass
class CaptionAnchor:
    page_number: int
    label: str  # "1", "2", "A" …
    raw_text: str
    kind: str = "figure"  # figure | table
    bbox_pdf: tuple[float, float, float, float] | None = None
    bbox_1000: tuple[int, int, int, int] | None = None
    char_offset: int | None = None


@dataclass
class FigureGroup:
    """Formal figure unit = one Fig./Figure number (may contain subplots)."""

    page_number: int
    figure_label: str  # "2" from "Fig. 2"
    caption: str = ""
    caption_anchor: CaptionAnchor | None = None
    subfigures: list[str] = field(default_factory=list)  # ["a","b","c","d"]
    member_candidate_ids: list[str] = field(default_factory=list)
    bbox_pdf: tuple[float, float, float, float] | None = None
    bbox_1000: tuple[int, int, int, int] | None = None
    display_index: int | None = None  # UI only — never identity
    force_pdf_clip: bool = True
    group_id: str = ""
    warnings: list[str] = field(default_factory=list)

    def ensure_id(self) -> str:
        if not self.group_id:
            cap = (self.caption or "")[:40]
            self.group_id = f"p{self.page_number:04d}_fig{self.figure_label}_{abs(hash(cap)) % 10_000_000:07d}"
        return self.group_id


@dataclass
class PageLayoutManifest:
    page_number: int
    pdf_hash: str = ""
    pipeline_version: str = LAYOUT_PIPELINE_VERSION
    spans: list[TextSpanStyle] = field(default_factory=list)
    captions: list[CaptionAnchor] = field(default_factory=list)
    image_candidates: list[dict[str, Any]] = field(default_factory=list)
    vector_candidates: list[dict[str, Any]] = field(default_factory=list)
    figure_groups: list[FigureGroup] = field(default_factory=list)
    plain_text: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "pdf_hash": self.pdf_hash,
            "pipeline_version": self.pipeline_version,
            "plain_text": self.plain_text,
            "warnings": list(self.warnings),
            "spans": [asdict(s) for s in self.spans],
            "captions": [asdict(c) for c in self.captions],
            "image_candidates": list(self.image_candidates),
            "vector_candidates": list(self.vector_candidates),
            "figure_groups": [asdict(g) for g in self.figure_groups],
        }
