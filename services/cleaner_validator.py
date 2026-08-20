"""Content preservation validator for Cleaner (format-only changes)."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from core.cleaner_models import ContentPreservationIssue, ContentPreservationResult
from services.transcription_validator import FIGURE_MARKER_RE
from utils.math_normalization import math_payloads_equivalent
from utils.table_normalization import table_payloads_equivalent

VALIDATOR_VERSION = "1"

_IMAGE_RE = re.compile(r"!\[[^\]]*]\([^)]+\)")
_URL_RE = re.compile(r"https?://[^\s)>\]]+")
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_MATH_BLOCK_RE = re.compile(
    r"\$\$.*?\$\$|\\\(.*?\\\)|\\\[.*?\\\]|(?<!\$)\$(?!\$).+?(?<!\$)\$(?!\$)",
    re.DOTALL,
)
_NUMERIC_RE = re.compile(
    r"(?<![A-Za-z_])[-+]?(?:\d+\.\d+|\d+)(?:[eE][-+]?\d+)?%?"
)


class CleanerValidator:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = ((config or {}).get("cleaner") or {}).get("validation") or {}
        self.require_images = bool(cfg.get("require_exact_image_refs", True))
        self.require_numeric = bool(cfg.get("require_exact_numeric_tokens", True))
        self.require_urls = bool(cfg.get("require_exact_urls", True))
        self.require_math = bool(cfg.get("require_math_payload_equivalence", True))
        self.require_table = bool(cfg.get("require_table_payload_equivalence", True))
        self.require_prose = bool(cfg.get("require_visible_prose_equivalence", True))
        self.version = VALIDATOR_VERSION

    def validate(self, *, source: str, cleaned: str) -> ContentPreservationResult:
        issues: list[ContentPreservationIssue] = []
        src = source or ""
        cln = cleaned or ""

        if not cln.strip():
            issues.append(
                ContentPreservationIssue("cleaned_empty", "BLOCKING", "cleaned empty")
            )

        if FIGURE_MARKER_RE.search(cln) or re.search(
            r"(?:<!--\s*)?FIGURE\s+page\s*=", cln, re.I
        ):
            issues.append(
                ContentPreservationIssue(
                    "unresolved_figure_reintroduced",
                    "BLOCKING",
                    "FIGURE marker reappeared",
                )
            )

        if self.require_images:
            src_imgs = _IMAGE_RE.findall(src)
            cln_imgs = _IMAGE_RE.findall(cln)
            if src_imgs != cln_imgs:
                issues.append(
                    ContentPreservationIssue(
                        "image_reference_changed",
                        "BLOCKING",
                        f"images {len(src_imgs)}→{len(cln_imgs)}",
                    )
                )

        if self.require_urls:
            if _URL_RE.findall(src) != _URL_RE.findall(cln):
                issues.append(
                    ContentPreservationIssue("url_changed", "BLOCKING", "URL sequence changed")
                )

        if self.require_numeric:
            if _NUMERIC_RE.findall(src) != _NUMERIC_RE.findall(cln):
                issues.append(
                    ContentPreservationIssue(
                        "numeric_content_changed",
                        "BLOCKING",
                        "numeric token sequence changed",
                    )
                )

        if self.require_math and not math_payloads_equivalent(src, cln):
            issues.append(
                ContentPreservationIssue(
                    "math_content_changed", "BLOCKING", "math payload changed"
                )
            )

        if self.require_table and not table_payloads_equivalent(src, cln):
            issues.append(
                ContentPreservationIssue(
                    "table_content_changed", "BLOCKING", "table cell payload changed"
                )
            )

        # code fence payloads (excluding outer wrapper already stripped)
        src_code = [m.group(0) for m in _CODE_FENCE_RE.finditer(src)]
        cln_code = [m.group(0) for m in _CODE_FENCE_RE.finditer(cln)]
        if src_code != cln_code:
            # allow if only difference is removed outer markdown fence (already in source compare)
            issues.append(
                ContentPreservationIssue(
                    "code_content_changed",
                    "WARNING",
                    "code fence payload differs",
                )
            )

        if self.require_prose:
            if self._visible_prose(src) != self._visible_prose(cln):
                issues.append(
                    ContentPreservationIssue(
                        "visible_prose_changed",
                        "BLOCKING",
                        "visible prose payload changed",
                    )
                )

        if any(i.severity == "BLOCKING" for i in issues):
            verdict = "BLOCKING"
        elif issues:
            verdict = "WARNING"
        else:
            verdict = "PASS"
        return ContentPreservationResult(verdict=verdict, issues=issues)

    def _visible_prose(self, text: str) -> str:
        s = text or ""
        s = _CODE_FENCE_RE.sub(" ", s)
        s = _MATH_BLOCK_RE.sub(" ", s)
        s = _IMAGE_RE.sub(" ", s)
        # strip table lines roughly
        lines = []
        for ln in s.splitlines():
            if ln.strip().startswith("|"):
                continue
            lines.append(ln)
        s = "\n".join(lines)
        s = re.sub(r"(?m)^#{1,6}\s*", "", s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
        s = re.sub(r"__([^_]+)__", r"\1", s)
        s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", s)
        s = unicodedata.normalize("NFKC", s)
        s = re.sub(r"\s+", "", s)
        return s
