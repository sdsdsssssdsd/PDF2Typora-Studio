"""Figure pipeline domain models (Phase 6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

FIGURE_PIPELINE_VERSION = "2"


class FigureSourceMethod(str, Enum):
    PDF_NATIVE = "pdf_native"
    PDF_CLIP = "pdf_clip"
    PAGE_RASTER_CROP = "page_raster_crop"
    MANUAL_CROP = "manual_crop"
    UNRESOLVED = "unresolved"


class FigureStatus(str, Enum):
    WAITING = "waiting"
    DISCOVERING = "discovering"
    MATCHED = "matched"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    RESOLVED = "resolved"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    SKIPPED = "skipped"
    CACHED = "cached"


@dataclass(frozen=True)
class FigureRequest:
    page_number: int
    figure_index: int
    marker: str
    figure_type: str
    caption: str | None
    ai_bbox_1000: tuple[int, int, int, int] | None
    mermaid_candidate: bool = False
    figure_label: str | None = None  # Fig. N — identity preferred over index
    force_pdf_clip: bool = False
    group_bbox_1000: tuple[int, int, int, int] | None = None


@dataclass
class FigureCandidate:
    candidate_id: str
    page_number: int
    candidate_type: str  # raster | vector | special
    bbox_pdf: tuple[float, float, float, float] | None
    bbox_1000: tuple[int, int, int, int] | None
    xref: int | None = None
    digest: str | None = None
    width: int | None = None
    height: int | None = None
    source: FigureSourceMethod = FigureSourceMethod.PDF_NATIVE
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FigureMatch:
    request: FigureRequest
    candidate: FigureCandidate | None
    iou: float | None = None
    containment: float | None = None
    center_distance: float | None = None
    score: float = 0.0
    strategy: str = ""
    auto_resolvable: bool = False
    reasons: list[str] = field(default_factory=list)


@dataclass
class FigureExtractionPlan:
    request: FigureRequest
    method: FigureSourceMethod
    clip_rect_pdf: tuple[float, float, float, float] | None
    candidate: FigureCandidate | None
    match_score: float
    crop_dpi: int
    padding_ratio: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class FigureArtifactResult:
    page_number: int
    figure_index: int
    artifact_path: str | None
    artifact_hash: str
    source_method: FigureSourceMethod
    width: int | None = None
    height: int | None = None
    cached: bool = False
    valid: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class MarkerConsistencyReport:
    ok: bool
    markers_in_md: list[int]
    figures_in_json: list[int]
    issues: list[str] = field(default_factory=list)
    needs_review: bool = False
    safe_marker_fix: bool = False
    loose_markers: list[Any] = field(default_factory=list)
    safe_repairs: list[Any] = field(default_factory=list)
    marker_repair_type: str | None = None


@dataclass
class PageFigureResult:
    page_number: int
    stage_status: str
    figures: list[dict[str, Any]] = field(default_factory=list)
    resolved_path: str | None = None
    cached: bool = False
    error: str | None = None
