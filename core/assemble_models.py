"""Assemble pipeline domain models (Phase 7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

ASSEMBLER_VERSION = "1"


class ContinuityPatchAction(str, Enum):
    NO_ACTION = "NO_ACTION"
    JOIN_WITH_SPACE = "JOIN_WITH_SPACE"
    JOIN_WITHOUT_SPACE = "JOIN_WITHOUT_SPACE"
    JOIN_WITH_NEWLINE = "JOIN_WITH_NEWLINE"
    CUSTOM_REPLACEMENT = "CUSTOM_REPLACEMENT"


class ContinuityCandidateStatus(str, Enum):
    UNREVIEWED = "UNREVIEWED"
    NO_ACTION = "NO_ACTION"
    PATCHED = "PATCHED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    STALE = "STALE"


class PageSourceType(str, Enum):
    RESOLVED = "resolved"
    CANONICAL = "canonical"


@dataclass(frozen=True)
class AssemblyRequest:
    project_root: Path
    page_numbers: tuple[int, ...]
    preserve_page_markers: bool = True
    apply_continuity_patches: bool = True
    allow_unresolved_figures: bool = False
    force: bool = False


@dataclass
class AssemblyResult:
    success: bool
    output_path: Path | None = None
    total_pages: int = 0
    resolved_sources: int = 0
    canonical_sources: int = 0
    continuity_candidates: int = 0
    continuity_patches_applied: int = 0
    unreviewed_continuity_candidates: int = 0
    warnings: list[str] = field(default_factory=list)
    assembly_hash: str | None = None
    cached: bool = False
    error: str | None = None
    report_path: str | None = None
    manifest_path: str | None = None


@dataclass
class PageSourceEntry:
    page: int
    source: str
    source_type: str
    sha256: str
    figure_count: int = 0


@dataclass
class ContinuityCandidate:
    left_page: int
    right_page: int
    left_tail: str
    right_head: str
    source_flags: list[str] = field(default_factory=list)
    suspicion_score: float = 0.0
    status: str = ContinuityCandidateStatus.UNREVIEWED.value


@dataclass
class ContinuityPatch:
    left_page: int
    right_page: int
    action: str
    custom_text: str | None = None
    left_context: str = ""
    right_context: str = ""
    source_hash_left: str = ""
    source_hash_right: str = ""
    manually_reviewed: bool = True
