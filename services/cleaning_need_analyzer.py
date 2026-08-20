"""Decide whether a page still needs AI format cleaning."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.cleaner_models import CleaningNeedReport
from services.deterministic_cleaner import DeterministicCleaner


_PAREN_MATH = re.compile(r"\\\(|\\\)")
_BRACKET_MATH = re.compile(r"\\\[|\\\]")
_EMPHASIS = re.compile(r"(?<!\*)\*[^*\n]+\*(?!\*)|\*\*[^*\n]+\*\*")
_BROKEN_TABLE = re.compile(r"(?m)^\s*\|[^|\n]*$")
_HR = re.compile(r"(?m)^---\s*$")
_SUSPICIOUS_HEADING = re.compile(r"(?m)^#{7,}\s")


class CleaningNeedAnalyzer:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}

    def analyze(
        self,
        *,
        page_number: int,
        cleaned_body: str,
        deterministic_issues: list[str] | None = None,
        project_root: Path | None = None,
    ) -> CleaningNeedReport:
        reasons: list[str] = []
        text = cleaned_body or ""

        for iss in deterministic_issues or []:
            reasons.append(iss)

        if _PAREN_MATH.search(text) or _BRACKET_MATH.search(text):
            # unpaired leftovers or unconverted
            reasons.append("math_delimiter_issue")
        if _EMPHASIS.search(text):
            reasons.append("markdown_emphasis")
        if _BROKEN_TABLE.search(text):
            reasons.append("broken_table")
        if _HR.search(text):
            reasons.append("standalone_horizontal_rule")
        if _SUSPICIOUS_HEADING.search(text):
            reasons.append("suspicious_heading_format")
        if text.lstrip().startswith("```"):
            reasons.append("outer_code_fence")

        if project_root is not None:
            js = project_root / "page_results" / f"page_{page_number:04d}.json"
            if js.exists():
                try:
                    payload = json.loads(js.read_text(encoding="utf-8"))
                    warnings = (payload.get("result") or {}).get("warnings") or []
                    if warnings:
                        reasons.append("transcription_warning")
                except (json.JSONDecodeError, OSError):
                    pass

        reasons = sorted(set(reasons))
        # emphasis alone is soft — still can be rules-only if nothing else
        hard = {
            "math_delimiter_issue",
            "broken_table",
            "outer_code_fence",
            "standalone_horizontal_rule",
            "suspicious_heading_format",
            "possible_formula_format_issue",
            "complex_table_layout",
        }
        needs_ai = bool(set(reasons) & hard)
        already_clean = not reasons
        return CleaningNeedReport(
            page_number=page_number,
            needs_ai=needs_ai,
            reasons=reasons,
            already_clean=already_clean,
        )
