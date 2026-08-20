"""Final validator — check only, never rewrite content."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.final_models import FINAL_VALIDATOR_VERSION
from services.assembled_markdown_validator import PAGE_MARKER_RE
from services.transcription_validator import FIGURE_MARKER_RE

_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_HR_RE = re.compile(r"(?m)^---\s*$")
_PAREN = re.compile(r"\\\(|\\\)")
_BRACKET = re.compile(r"\\\[|\\\]")
_BEGIN = re.compile(r"\\begin\{([^}]+)\}")
_END = re.compile(r"\\end\{([^}]+)\}")
_DOLLAR_DISPLAY = re.compile(r"\$\$")
_ABS_PATH = re.compile(r"(?i)(?:[A-Z]:\\|file:///|\\\\)")
_UNSAFE = re.compile(
    r"(?i)(?:\.\./|\.cache/|tmp/|experiments/|history/|resolved_pages/|workspace/)"
)
_PLACEHOLDER = re.compile(r"(?i)TODO:|FIXME:|<<<|>>>|PLACEHOLDER")
_HTTP_IMG = re.compile(r"(?i)^https?://")


@dataclass
class FinalValidationResult:
    ok: bool
    status: str = "fail"
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    release_warnings: list[str] = field(default_factory=list)
    page_markers: int = 0
    figure_markers: int = 0
    horizontal_rules: int = 0
    image_links_total: int = 0
    image_links_valid: int = 0
    image_links_missing: list[str] = field(default_factory=list)
    absolute_paths: int = 0
    unsafe_paths: int = 0
    math_warnings: list[str] = field(default_factory=list)
    table_warnings: list[str] = field(default_factory=list)
    validator_version: str = FINAL_VALIDATOR_VERSION


class FinalValidator:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        final_cfg = (config or {}).get("final") or {}
        self.rules = final_cfg.get("validation") or {}
        self.version = FINAL_VALIDATOR_VERSION

    def validate(
        self, *, project_root: Path, clean_path: Path | None = None
    ) -> FinalValidationResult:
        path = clean_path or project_root / "intermediate" / "clean.md"
        blocking: list[str] = []
        warnings: list[str] = []
        math_warnings: list[str] = []
        table_warnings: list[str] = []

        if not path.exists():
            return FinalValidationResult(
                ok=False, status="fail", blocking=["clean_md_missing"]
            )
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return FinalValidationResult(
                ok=False, status="fail", blocking=["clean_not_utf8"]
            )

        if not text.strip():
            blocking.append("clean_empty")
        if path.stat().st_size < 20:
            blocking.append("clean_suspiciously_small")

        page_markers = len(PAGE_MARKER_RE.findall(text))
        figure_markers = len(FIGURE_MARKER_RE.findall(text))
        if self.rules.get("require_no_page_markers", True) and page_markers:
            blocking.append(f"page_markers:{page_markers}")
        if self.rules.get("require_no_figure_markers", True) and figure_markers:
            blocking.append(f"figure_markers:{figure_markers}")

        hrs = _HR_RE.findall(text)
        if self.rules.get("block_horizontal_rules", True) and hrs:
            blocking.append(f"horizontal_rules:{len(hrs)}")

        if _PLACEHOLDER.search(text):
            blocking.append("placeholder_found")

        stripped = text.strip()
        if stripped.startswith("```") and stripped.endswith("```"):
            first = stripped.splitlines()[0].strip().lower()
            if first in {"```", "```markdown", "```md"}:
                blocking.append("outer_markdown_fence")

        if self.rules.get("validate_math_balance", True):
            if _PAREN.search(text) or _BRACKET.search(text):
                math_warnings.append("paren_or_bracket_delimiters_present")
                blocking.append("legacy_math_delimiters")

            display_count = len(_DOLLAR_DISPLAY.findall(text))
            if display_count % 2:
                math_warnings.append("unbalanced_display_dollars")
                blocking.append("unbalanced_display_dollars")

            without_display = _DOLLAR_DISPLAY.sub("", text)
            singles = without_display.count("$")
            if singles % 2:
                math_warnings.append("unbalanced_inline_dollars")
                blocking.append("unbalanced_inline_dollars")

            begins = _BEGIN.findall(text)
            ends = _END.findall(text)
            if sorted(begins) != sorted(ends):
                math_warnings.append("unbalanced_latex_environments")
                blocking.append("unbalanced_latex_environments")
            if begins.count("aligned") != ends.count("aligned"):
                blocking.append("unbalanced_aligned")

        images = _IMAGE_RE.findall(text)
        missing: list[str] = []
        abs_count = 0
        unsafe = 0
        valid = 0
        for _alt, target in images:
            t = target.strip()
            if _HTTP_IMG.match(t):
                abs_count += 1
                if self.rules.get("block_absolute_paths", True):
                    blocking.append(f"http_image_path:{t}")
                continue
            if _ABS_PATH.search(t) or (t.startswith("/") and not t.startswith("./")):
                abs_count += 1
                if self.rules.get("block_absolute_paths", True):
                    blocking.append(f"absolute_image_path:{t}")
                continue
            if _UNSAFE.search(t) or t.endswith(".tmp") or t.endswith(".part"):
                unsafe += 1
                if self.rules.get("block_unsafe_paths", True):
                    blocking.append(f"unsafe_image_path:{t}")
                continue
            if self.rules.get("require_relative_images", True) and not t.startswith(
                "figures/"
            ):
                blocking.append(f"non_figures_image:{t}")
                continue
            resolved = (project_root / t).resolve()
            figures_root = (project_root / "figures").resolve()
            try:
                resolved.relative_to(figures_root)
            except ValueError:
                unsafe += 1
                blocking.append(f"path_traversal:{t}")
                continue
            if not resolved.exists() or resolved.stat().st_size == 0:
                missing.append(t)
                if self.rules.get("require_all_images", True):
                    blocking.append(f"missing_image:{t}")
            else:
                valid += 1

        for line in text.splitlines():
            if line.strip().startswith("|") and line.count("|") < 2:
                table_warnings.append("possible_broken_table_row")
                break

        release = ["math_heavy_validation_pending"]
        ok = not blocking
        return FinalValidationResult(
            ok=ok,
            status="pass" if ok else "fail",
            blocking=sorted(set(blocking)),
            warnings=warnings,
            release_warnings=release,
            page_markers=page_markers,
            figure_markers=figure_markers,
            horizontal_rules=len(hrs),
            image_links_total=len(images),
            image_links_valid=valid,
            image_links_missing=missing,
            absolute_paths=abs_count,
            unsafe_paths=unsafe,
            math_warnings=math_warnings,
            table_warnings=table_warnings,
        )
