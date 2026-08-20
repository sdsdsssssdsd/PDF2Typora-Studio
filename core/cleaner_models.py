"""Cleaner domain models (Phase 8)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


CLEANER_PIPELINE_VERSION = "1"
DETERMINISTIC_CLEANER_VERSION = "1"
CLEAN_DOCUMENT_BUILDER_VERSION = "1"


class CleanerMode(str, Enum):
    SAFE_RULES_ONLY = "safe_rules_only"
    SMART = "smart"
    FULL_AI = "full_ai"


class CleanAcceptanceMode(str, Enum):
    RULES = "rules"
    AI = "ai"
    KEEP_SOURCE = "keep_source"
    MANUAL = "manual"
    CACHED = "cached"


@dataclass
class RawPageFragment:
    page_number: int
    body: str
    source_hash: str
    marker: str = ""


@dataclass
class DeterministicCleanResult:
    page_number: int
    cleaned: str
    actions: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


@dataclass
class CleaningNeedReport:
    page_number: int
    needs_ai: bool
    reasons: list[str] = field(default_factory=list)
    already_clean: bool = False


@dataclass
class ContentPreservationIssue:
    code: str
    severity: str  # BLOCKING | WARNING | INFO
    message: str = ""


@dataclass
class ContentPreservationResult:
    verdict: str  # PASS | WARNING | BLOCKING
    issues: list[ContentPreservationIssue] = field(default_factory=list)

    @property
    def blocking(self) -> list[ContentPreservationIssue]:
        return [i for i in self.issues if i.severity == "BLOCKING"]

    @property
    def ok(self) -> bool:
        return len(self.blocking) == 0


@dataclass
class PageCleanResult:
    page_number: int
    stage_status: str
    acceptance_mode: str | None = None
    cleaned_path: str | None = None
    cached: bool = False
    needs_ai: bool = False
    ai_called: bool = False
    warnings: list[str] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class CleanDocumentResult:
    success: bool
    clean_traced_path: Path | None = None
    clean_path: Path | None = None
    cached: bool = False
    page_count: int = 0
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    document_hash: str | None = None
