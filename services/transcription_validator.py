"""Semantic validation for page transcription results."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from ai.schemas.transcription import PageTranscriptionResult, VALIDATOR_VERSION
from core.models import ValidationSeverity
from services.prompt_leak_detector import detect_prompt_leak, is_prompt_leak

FIGURE_MARKER_RE = re.compile(
    r"<!--\s*FIGURE\s+page=(\d+)\s+index=(\d+)\s*-->",
    re.IGNORECASE,
)
IMAGE_MD_RE = re.compile(r"!\[[^\]]*]\([^)]+\)")

BLOCKING_CODES = {
    "prompt_leak_detected",
    "invented_image_reference",
    "figure_marker_mismatch",
    "invalid_page_number",
    "output_truncated",
    "markdown_empty",
    "schema_invalid",
    "formula_delimiter_unbalanced",
}


@dataclass
class ValidationIssue:
    code: str
    severity: ValidationSeverity
    message: str


@dataclass
class ValidationReport:
    ok: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
    needs_review: bool = False
    validator_version: str = VALIDATOR_VERSION

    @property
    def blocking(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.BLOCKING]

    def can_auto_accept(self, result: PageTranscriptionResult) -> bool:
        if not self.ok or self.blocking:
            return False
        if result.needs_review or self.needs_review:
            return False
        if any(i.severity == ValidationSeverity.WARNING for i in self.issues):
            # conservative: complex_table and similar go to review
            return False
        return True

    def merge_into(self, result: PageTranscriptionResult) -> PageTranscriptionResult:
        warnings = list(result.warnings) + self.warnings
        needs = result.needs_review or self.needs_review or bool(self.errors) or bool(
            self.blocking
        )
        return result.model_copy(update={"warnings": warnings, "needs_review": needs})

    def _add(
        self,
        code: str,
        severity: ValidationSeverity,
        message: str | None = None,
    ) -> None:
        self.issues.append(
            ValidationIssue(code=code, severity=severity, message=message or code)
        )
        if severity == ValidationSeverity.BLOCKING:
            self.errors.append(code)
            self.ok = False if code in {"markdown_empty", "schema_invalid"} else self.ok
            self.needs_review = True
        else:
            self.warnings.append(code)
            self.needs_review = True


class TranscriptionValidator:
    def validate(
        self,
        result: PageTranscriptionResult,
        *,
        requested_page: int,
    ) -> ValidationReport:
        report = ValidationReport(ok=True)
        md = result.markdown or ""

        if result.page_number != requested_page:
            report._add(
                "invalid_page_number",
                ValidationSeverity.BLOCKING,
                f"got {result.page_number}, expected {requested_page}",
            )

        if not md.strip():
            report._add("markdown_empty", ValidationSeverity.BLOCKING)
        elif len(md.strip()) < 20:
            report._add("extremely_short_markdown", ValidationSeverity.WARNING)

        stripped = md.strip()
        if stripped.startswith("```"):
            report._add("outer_markdown_fence", ValidationSeverity.BLOCKING)

        for line in md.splitlines():
            if line.strip() == "---":
                report._add("horizontal_rule_line", ValidationSeverity.BLOCKING)
                break

        if IMAGE_MD_RE.search(md):
            report._add("invented_image_reference", ValidationSeverity.BLOCKING)

        if is_prompt_leak(md):
            hits = detect_prompt_leak(md)
            report._add(
                "prompt_leak_detected",
                ValidationSeverity.BLOCKING,
                ", ".join(hits[:5]),
            )

        no_math = re.sub(r"\$\$.*?\$\$", "", md, flags=re.DOTALL)
        no_math = re.sub(r"\$.*?\$", "", no_math)
        if re.search(r"\*\*[^*\n]+\*\*", no_math) or re.search(
            r"(?<!\*)\*[^*\n]+\*(?!\*)", no_math
        ):
            report._add("possible_emphasis_asterisk", ValidationSeverity.WARNING)

        if md.count("$$") % 2 != 0:
            report._add("formula_delimiter_unbalanced", ValidationSeverity.BLOCKING)
        dollar_count = md.count("$") - md.count("$$") * 2
        if dollar_count % 2 != 0:
            report._add("formula_delimiter_unbalanced", ValidationSeverity.BLOCKING)

        self._check_figures(result, requested_page, report)

        if "complex_table_layout" in (result.warnings or []):
            report._add("complex_table_layout", ValidationSeverity.WARNING)

        return report

    def _check_figures(
        self,
        result: PageTranscriptionResult,
        requested_page: int,
        report: ValidationReport,
    ) -> None:
        markers = FIGURE_MARKER_RE.findall(result.markdown or "")
        marker_indices = {int(idx) for _, idx in markers}
        for page_s, idx in markers:
            if int(page_s) != requested_page:
                report._add(
                    "figure_marker_mismatch",
                    ValidationSeverity.BLOCKING,
                    f"page={page_s} index={idx}",
                )

        fig_indices = [f.figure_index for f in result.figures]
        if len(fig_indices) != len(set(fig_indices)):
            report._add("figure_marker_mismatch", ValidationSeverity.BLOCKING)

        for fig in result.figures:
            if fig.figure_index not in marker_indices:
                report._add(
                    "figure_marker_mismatch",
                    ValidationSeverity.BLOCKING,
                    f"missing marker index={fig.figure_index}",
                )
        for idx in marker_indices:
            if idx not in fig_indices:
                report._add(
                    "figure_marker_mismatch",
                    ValidationSeverity.BLOCKING,
                    f"missing record index={idx}",
                )

        for fig in result.figures:
            if fig.bbox_1000 is not None:
                x1, y1, x2, y2 = fig.bbox_1000
                if not (0 <= x1 <= x2 <= 1000 and 0 <= y1 <= y2 <= 1000):
                    report._add("bbox_uncertain", ValidationSeverity.WARNING)
