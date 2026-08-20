"""Structural validation for assembled raw.md (no content rewriting)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from services.transcription_validator import FIGURE_MARKER_RE

PAGE_MARKER_RE = re.compile(r"<!--\s*PAGE:\s*(\d+)\s*-->", re.IGNORECASE)
IMAGE_FIG_RE = re.compile(r"!\[[^\]]*]\((figures/[^)]+)\)")
HR_RE = re.compile(r"(?m)^---\s*$")


@dataclass
class RawValidationResult:
    ok: bool
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    page_markers: list[int] = field(default_factory=list)
    unresolved_figure_markers: int = 0
    figure_links: int = 0
    missing_figure_paths: list[str] = field(default_factory=list)
    horizontal_rules: int = 0


class AssembledMarkdownValidator:
    def validate(
        self,
        *,
        raw_md: str,
        project_root: Path,
        expected_pages: list[int],
        allow_unresolved_figures: bool = False,
    ) -> RawValidationResult:
        blocking: list[str] = []
        warnings: list[str] = []

        if not (raw_md or "").strip():
            blocking.append("raw_empty")
            return RawValidationResult(ok=False, blocking=blocking)

        markers = [int(m) for m in PAGE_MARKER_RE.findall(raw_md)]
        expected = sorted(expected_pages)

        if len(markers) != len(expected):
            blocking.append(
                f"page_marker_count_mismatch:got={len(markers)} expected={len(expected)}"
            )
        if markers != expected:
            if sorted(markers) != expected:
                blocking.append("page_markers_missing_or_extra")
            elif markers != expected:
                blocking.append("page_markers_not_ascending")

        seen: set[int] = set()
        for p in markers:
            if p in seen:
                blocking.append(f"duplicate_page_marker:{p}")
            seen.add(p)

        unresolved = FIGURE_MARKER_RE.findall(raw_md)
        # Also catch loose FIGURE markers that slipped through
        loose = re.findall(
            r"(?:<!--\s*)?FIGURE\s+page\s*=\s*\d+\s+index\s*=\s*\d+",
            raw_md,
            flags=re.IGNORECASE,
        )
        unresolved_count = max(len(unresolved), len(loose))
        if unresolved_count and not allow_unresolved_figures:
            blocking.append(f"unresolved_figure_markers:{unresolved_count}")
        elif unresolved_count:
            warnings.append(f"assembled_with_unresolved_figures:{unresolved_count}")

        missing_paths: list[str] = []
        links = IMAGE_FIG_RE.findall(raw_md)
        for rel in links:
            path = project_root / rel
            if not path.exists():
                missing_paths.append(rel)
                blocking.append(f"missing_figure_artifact:{rel}")

        hrs = HR_RE.findall(raw_md)
        if hrs:
            warnings.append(f"horizontal_rules_in_source:{len(hrs)}")

        return RawValidationResult(
            ok=not blocking,
            blocking=blocking,
            warnings=warnings,
            page_markers=markers,
            unresolved_figure_markers=unresolved_count,
            figure_links=len(links),
            missing_figure_paths=missing_paths,
            horizontal_rules=len(hrs),
        )
