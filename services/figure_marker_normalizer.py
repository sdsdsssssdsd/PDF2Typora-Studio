"""Detect and normalize loose FIGURE markers (Phase 6.5)."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Recognizes strict HTML comments and plain-text drift variants.
MARKER_LIKE_RE = re.compile(
    r"(?:<!--\s*)?FIGURE\s+page\s*=\s*(\d+)\s+index\s*=\s*(\d+)\s*(?:\s*-->)?",
    re.IGNORECASE,
)

STRICT_CANONICAL_RE = re.compile(
    r"<!-- FIGURE page=(\d+) index=(\d+) -->",
    re.IGNORECASE,
)


def canonical_marker(page: int, index: int) -> str:
    return f"<!-- FIGURE page={page} index={index} -->"


@dataclass(frozen=True)
class LooseMarkerMatch:
    start: int
    end: int
    page: int
    index: int
    original: str
    normalized: str
    is_strict: bool


class FigureMarkerNormalizer:
    def find_markers(self, markdown: str) -> list[LooseMarkerMatch]:
        text = markdown or ""
        out: list[LooseMarkerMatch] = []
        for m in MARKER_LIKE_RE.finditer(text):
            page = int(m.group(1))
            index = int(m.group(2))
            original = m.group(0)
            norm = canonical_marker(page, index)
            strict = original == norm or bool(STRICT_CANONICAL_RE.fullmatch(original))
            out.append(
                LooseMarkerMatch(
                    start=m.start(),
                    end=m.end(),
                    page=page,
                    index=index,
                    original=original,
                    normalized=norm,
                    is_strict=strict,
                )
            )
        return out

    def apply_repairs(self, markdown: str, repairs: list[LooseMarkerMatch]) -> str:
        """Replace marker spans with normalized form (resolved working copy only)."""
        if not repairs:
            return markdown
        text = markdown
        for r in sorted(repairs, key=lambda x: x.start, reverse=True):
            text = text[: r.start] + r.normalized + text[r.end :]
        return text

    def syntax_only_repairs(
        self, markers: list[LooseMarkerMatch]
    ) -> list[LooseMarkerMatch]:
        return [m for m in markers if not m.is_strict]
