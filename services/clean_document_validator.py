"""Validate final clean.md / clean_traced.md documents."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from services.assembled_markdown_validator import PAGE_MARKER_RE
from services.raw_page_splitter import RawPageSplitter
from services.transcription_validator import FIGURE_MARKER_RE
from utils.math_normalization import math_payloads_equivalent
from utils.table_normalization import table_payloads_equivalent

_IMAGE_RE = re.compile(r"!\[[^\]]*]\((figures/[^)]+)\)")
_URL_RE = re.compile(r"https?://[^\s)>\]]+")
_NUMERIC_RE = re.compile(
    r"(?<![A-Za-z_])[-+]?(?:\d+\.\d+|\d+)(?:[eE][-+]?\d+)?%?"
)
_HR_RE = re.compile(r"(?m)^---\s*$")
_PAREN = re.compile(r"\\\(|\\\)")
_BRACKET = re.compile(r"\\\[|\\\]")


@dataclass
class CleanDocumentValidation:
    ok: bool
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class CleanDocumentValidator:
    def validate(
        self,
        *,
        project_root: Path,
        expected_pages: list[int],
        raw_path: Path | None = None,
        traced_path: Path | None = None,
        clean_path: Path | None = None,
    ) -> CleanDocumentValidation:
        blocking: list[str] = []
        warnings: list[str] = []
        raw_path = raw_path or project_root / "intermediate" / "raw.md"
        traced_path = traced_path or project_root / "intermediate" / "clean_traced.md"
        clean_path = clean_path or project_root / "intermediate" / "clean.md"

        for p in expected_pages:
            if not (project_root / "clean_pages" / f"page_{p:04d}.md").exists():
                blocking.append(f"missing_clean_page:{p}")

        if not clean_path.exists() or not clean_path.read_text(encoding="utf-8").strip():
            blocking.append("clean_empty")
            return CleanDocumentValidation(ok=False, blocking=blocking)

        clean = clean_path.read_text(encoding="utf-8")
        if PAGE_MARKER_RE.search(clean):
            blocking.append("clean_has_page_markers")
        if FIGURE_MARKER_RE.search(clean) or re.search(
            r"(?:<!--\s*)?FIGURE\s+page\s*=", clean, re.I
        ):
            blocking.append("clean_has_figure_markers")
        if _PAREN.search(clean) or _BRACKET.search(clean):
            warnings.append("paren_or_bracket_math_remaining")
        if _HR_RE.search(clean):
            warnings.append("horizontal_rules_present")

        imgs = _IMAGE_RE.findall(clean)
        for rel in imgs:
            if not (project_root / rel).exists():
                blocking.append(f"missing_figure:{rel}")

        if traced_path.exists():
            traced = traced_path.read_text(encoding="utf-8")
            markers = [int(m) for m in PAGE_MARKER_RE.findall(traced)]
            if markers != sorted(expected_pages):
                blocking.append("traced_page_markers_mismatch")

        if raw_path.exists():
            raw = raw_path.read_text(encoding="utf-8")
            compare = (
                traced_path.read_text(encoding="utf-8")
                if traced_path.exists()
                else clean
            )
            # Strip PAGE markers before document-level token audits
            raw_audit = PAGE_MARKER_RE.sub("", raw)
            clean_audit = PAGE_MARKER_RE.sub("", compare)

            raw_imgs = _IMAGE_RE.findall(raw_audit)
            cln_imgs = _IMAGE_RE.findall(clean)
            if raw_imgs != cln_imgs:
                blocking.append("document_image_refs_changed")
            if _URL_RE.findall(raw_audit) != _URL_RE.findall(clean_audit):
                blocking.append("document_urls_changed")
            if _NUMERIC_RE.findall(raw_audit) != _NUMERIC_RE.findall(clean_audit):
                blocking.append("document_numeric_changed")
            if not math_payloads_equivalent(raw_audit, clean_audit):
                blocking.append("document_math_changed")
            if not table_payloads_equivalent(raw_audit, clean_audit):
                blocking.append("document_table_changed")

        return CleanDocumentValidation(
            ok=not blocking, blocking=blocking, warnings=warnings
        )
