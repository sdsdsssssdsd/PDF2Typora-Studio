"""Domain enums and dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class PageStatus(str, Enum):
    WAITING = "WAITING"
    RENDERING = "RENDERING"
    RENDERED = "RENDERED"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    SKIPPED = "SKIPPED"


class StageStatus(str, Enum):
    WAITING = "waiting"
    RUNNING = "running"
    SUCCESS = "success"
    CACHED = "cached"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE = "stale"
    NEEDS_REVIEW = "needs_review"


class BatchRunStatus(str, Enum):
    CREATED = "CREATED"
    QUALIFYING_MODEL = "QUALIFYING_MODEL"
    WARMING_MODEL = "WARMING_MODEL"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_REVIEW = "COMPLETED_WITH_REVIEW"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


class BatchItemStatus(str, Enum):
    WAITING = "WAITING"
    RUNNING = "RUNNING"
    CACHED = "CACHED"
    AUTO_ACCEPTED = "AUTO_ACCEPTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


class ModelQualification(str, Enum):
    UNTESTED = "UNTESTED"
    QUALIFIED = "QUALIFIED"
    LIMITED = "LIMITED"
    DISABLED = "DISABLED"


class ValidationSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


class PipelineStage(str, Enum):
    RENDER = "render"
    TRANSCRIBE = "transcribe"
    FIGURES = "figures"
    ASSEMBLE = "assemble"
    CLEAN = "clean"
    VALIDATE = "validate"


class PipelineState(str, Enum):
    IDLE = "IDLE"
    PROJECT_READY = "PROJECT_READY"
    RENDERING = "RENDERING"
    TRANSCRIBING = "TRANSCRIBING"
    EXTRACTING_FIGURES = "EXTRACTING_FIGURES"
    ASSEMBLING = "ASSEMBLING"
    CLEANING = "CLEANING"
    VALIDATING = "VALIDATING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    COMPLETED = "COMPLETED"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


RENDER_PIPELINE_VERSION = "1"


@dataclass(frozen=True)
class RenderSettings:
    dpi: int = 200
    image_format: str = "png"
    alpha: bool = False
    colorspace: str = "rgb"


@dataclass(frozen=True)
class RenderRequest:
    pdf_path: Path
    output_dir: Path
    pages: tuple[int, ...]
    settings: RenderSettings
    force: bool = False
    pdf_hash: str = ""
    db_path: Path | None = None


@dataclass
class PageRenderResult:
    page_number: int
    image_path: Path | None
    width_px: int | None
    height_px: int | None
    settings_hash: str
    cached: bool
    success: bool
    error: str | None = None
    cancelled: bool = False


@dataclass(frozen=True)
class TranscriptionOptions:
    temperature: float = 0.0
    num_ctx: int | None = None
    think: bool | None = False
    keep_alive: str | int | None = "5m"
    schema_retry_attempts: int = 1
    use_cache: bool = True
    force: bool = False


@dataclass
class PDFInfo:
    file_path: Path
    file_name: str
    file_size: int
    page_count: int
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class ProjectInfo:
    name: str
    root_path: Path
    source_pdf: Path
    db_path: Path
    page_count: int
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict[str, Any] = field(default_factory=dict)


def page_number_to_index(page_number: int) -> int:
    """Convert 1-based user page number to 0-based PyMuPDF index."""
    if page_number < 1:
        raise ValueError(f"page_number must be >= 1, got {page_number}")
    return page_number - 1


def index_to_page_number(index: int) -> int:
    """Convert 0-based PyMuPDF index to 1-based user page number."""
    if index < 0:
        raise ValueError(f"index must be >= 0, got {index}")
    return index + 1
