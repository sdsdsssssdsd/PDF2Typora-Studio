"""Reconstruct bold / color from PDF spans into Markdown / Typora HTML."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.layout_models import TextSpanStyle


@dataclass
class StyleReconstructionResult:
    markdown_fragments: list[str] = field(default_factory=list)
    applied_bold: int = 0
    applied_color: int = 0
    warnings: list[str] = field(default_factory=list)


class StyleReconstructor:
    """
    PDF-sourced styles only.

    Rule: models must not invent bold; if PDF span is bold → **...**.
    Color via Typora-supported <span style=\"color:#rrggbb\">.
    """

    version = "1"

    def __init__(self, *, min_color_contrast: bool = True) -> None:
        self.min_color_contrast = min_color_contrast

    def span_to_markdown(self, span: TextSpanStyle) -> str:
        text = span.text
        if not text.strip():
            return text
        out = text
        # bold from PDF only
        if span.bold and not (out.startswith("**") and out.endswith("**")):
            # avoid wrapping whitespace-only edges oddly
            core = out.strip()
            if core and not re.fullmatch(r"[\W_]+", core):
                lead = out[: len(out) - len(out.lstrip())]
                trail = out[len(out.rstrip()) :]
                out = f"{lead}**{core}**{trail}"
        color = (span.color_hex or "").lower()
        if color and color not in {"#000000", "#000", "#010101"}:
            if span.bold and out.strip().startswith("**"):
                # wrap colored bold as HTML with markdown bold inside
                core = out.strip()
                lead = out[: len(out) - len(out.lstrip())]
                trail = out[len(out.rstrip()) :]
                out = f'{lead}<span style="color:{color}">{core}</span>{trail}'
            else:
                core = out.strip()
                lead = out[: len(out) - len(out.lstrip())]
                trail = out[len(out.rstrip()) :]
                out = f'{lead}<span style="color:{color}">{core}</span>{trail}'
        return out

    def reconstruct_plain_styled(self, spans: list[TextSpanStyle]) -> StyleReconstructionResult:
        fragments: list[str] = []
        bold = 0
        color = 0
        prev_block = -1
        prev_line = -1
        for s in spans:
            if s.block_no != prev_block and fragments:
                fragments.append("\n\n")
            elif s.line_no != prev_line and fragments:
                fragments.append("\n")
            frag = self.span_to_markdown(s)
            if s.bold:
                bold += 1
            if s.color_hex.lower() not in {"#000000", "#000", "#010101"}:
                color += 1
            fragments.append(frag)
            prev_block = s.block_no
            prev_line = s.line_no
        return StyleReconstructionResult(
            markdown_fragments=fragments,
            applied_bold=bold,
            applied_color=color,
        )

    def styled_document(self, spans: list[TextSpanStyle]) -> str:
        return "".join(self.reconstruct_plain_styled(spans).markdown_fragments)
