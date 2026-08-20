"""Compare PDF text layer coverage vs Markdown."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+")
_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    t = text.lower()
    t = re.sub(r"<!--.*?-->", " ", t, flags=re.S)
    t = re.sub(r"!\[[^\]]*]\([^)]*\)", " ", t)
    t = re.sub(r"`[^`]*`", " ", t)
    t = re.sub(r"\$\$[^$]*\$\$", " ", t)
    t = re.sub(r"\$[^$]*\$", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = _WS_RE.sub(" ", t)
    return t.strip()


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(_normalize(text))


@dataclass
class TextCoverageReport:
    ok: bool
    pdf_coverage: float
    markdown_coverage: float
    pdf_tokens: int
    markdown_tokens: int
    missing_tokens: list[str] = field(default_factory=list)
    extra_ratio: float = 0.0
    needs_review: bool = False
    issues: list[str] = field(default_factory=list)


class TextCoverageValidator:
    version = "1"

    def __init__(
        self,
        *,
        min_pdf_coverage: float = 0.85,
        min_markdown_coverage: float = 0.80,
    ) -> None:
        self.min_pdf_coverage = min_pdf_coverage
        self.min_markdown_coverage = min_markdown_coverage

    def validate(self, *, pdf_text: str, markdown: str) -> TextCoverageReport:
        pdf_toks = _tokens(pdf_text)
        md_toks = _tokens(markdown)
        if not pdf_toks:
            return TextCoverageReport(
                ok=True,
                pdf_coverage=1.0,
                markdown_coverage=1.0,
                pdf_tokens=0,
                markdown_tokens=len(md_toks),
                issues=["no_pdf_text_layer"],
            )

        pdf_set = set(pdf_toks)
        md_set = set(md_toks)
        # Coverage of PDF tokens present in markdown
        hit = sum(1 for t in pdf_toks if t in md_set)
        pdf_cov = hit / max(len(pdf_toks), 1)
        # Coverage of markdown tokens present in PDF (approx)
        hit_md = sum(1 for t in md_toks if t in pdf_set)
        md_cov = hit_md / max(len(md_toks), 1) if md_toks else 0.0

        missing = []
        seen = set()
        for t in pdf_toks:
            if t not in md_set and t not in seen and len(t) > 2:
                missing.append(t)
                seen.add(t)
            if len(missing) >= 20:
                break

        issues: list[str] = []
        if pdf_cov < self.min_pdf_coverage:
            issues.append(f"pdf_coverage_low:{pdf_cov:.3f}")
        if md_toks and md_cov < self.min_markdown_coverage:
            issues.append(f"markdown_coverage_low:{md_cov:.3f}")

        return TextCoverageReport(
            ok=not issues,
            pdf_coverage=round(pdf_cov, 4),
            markdown_coverage=round(md_cov, 4),
            pdf_tokens=len(pdf_toks),
            markdown_tokens=len(md_toks),
            missing_tokens=missing,
            needs_review=bool(issues),
            issues=issues,
        )
