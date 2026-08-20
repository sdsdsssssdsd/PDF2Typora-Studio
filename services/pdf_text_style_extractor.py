"""Extract PDF text spans with font / bold / color / bbox (PyMuPDF rawdict)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf

from core.layout_models import TextSpanStyle
from utils.geometry import page_rect_to_bbox_1000
from utils.logger import get_logger

logger = get_logger("pdf_text_style")

# PyMuPDF text span flags
_FLAG_SUPERSCRIPT = 1 << 0
_FLAG_ITALIC = 1 << 1
_FLAG_SERIFED = 1 << 2
_FLAG_MONOSPACED = 1 << 3
_FLAG_BOLD = 1 << 4


def _color_to_hex(color: int | float | None) -> str:
    if color is None:
        return "#000000"
    try:
        c = int(color)
    except (TypeError, ValueError):
        return "#000000"
    # PyMuPDF: sRGB as int 0xRRGGBB
    r = (c >> 16) & 255
    g = (c >> 8) & 255
    b = c & 255
    return f"#{r:02x}{g:02x}{b:02x}"


def _is_bold(flags: int, font: str) -> bool:
    if flags & _FLAG_BOLD:
        return True
    fl = (font or "").lower()
    return any(tok in fl for tok in ("bold", "black", "heavy", "semibold"))


def _is_italic(flags: int, font: str) -> bool:
    if flags & _FLAG_ITALIC:
        return True
    fl = (font or "").lower()
    return "italic" in fl or "oblique" in fl


class PDFTextStyleExtractor:
    version = "1"

    def extract_page(
        self, page: pymupdf.Page, *, page_number: int | None = None
    ) -> list[TextSpanStyle]:
        spans: list[TextSpanStyle] = []
        try:
            # Prefer "dict" (has span["text"]); "rawdict" uses chars[] without text
            raw = page.get_text("dict")
        except Exception as exc:  # noqa: BLE001
            logger.warning("dict text extract failed: %s", exc)
            return spans

        for bi, block in enumerate(raw.get("blocks") or []):
            if block.get("type", 0) != 0:
                continue
            for li, line in enumerate(block.get("lines") or []):
                for si, span in enumerate(line.get("spans") or []):
                    text = span.get("text")
                    if text is None and span.get("chars"):
                        text = "".join(
                            str(ch.get("c") or "") for ch in (span.get("chars") or [])
                        )
                    text = text or ""
                    if not text:
                        continue
                    bbox = span.get("bbox") or (0, 0, 0, 0)
                    rect = pymupdf.Rect(bbox)
                    flags = int(span.get("flags") or 0)
                    font = str(span.get("font") or "")
                    color = span.get("color")
                    spans.append(
                        TextSpanStyle(
                            text=text,
                            font=font,
                            size=float(span.get("size") or 0),
                            flags=flags,
                            color_int=int(color) if color is not None else 0,
                            color_hex=_color_to_hex(color),
                            bold=_is_bold(flags, font),
                            italic=_is_italic(flags, font),
                            bbox_pdf=(rect.x0, rect.y0, rect.x1, rect.y1),
                            bbox_1000=page_rect_to_bbox_1000(rect, page),
                            block_no=bi,
                            line_no=li,
                            span_no=si,
                        )
                    )
        return spans

    def extract_plain_text(self, page: pymupdf.Page) -> str:
        """Reading-order-ish plain text from spans (block order)."""
        spans = self.extract_page(page)
        if not spans:
            return (page.get_text("text") or "").strip()
        parts: list[str] = []
        prev_block = -1
        prev_line = -1
        for s in spans:
            if s.block_no != prev_block and parts:
                parts.append("\n\n")
            elif s.line_no != prev_line and parts:
                parts.append("\n")
            parts.append(s.text)
            prev_block = s.block_no
            prev_line = s.line_no
        return "".join(parts).strip()

    def extract_from_pdf(
        self, pdf_path: Path, page_number: int
    ) -> tuple[list[TextSpanStyle], str]:
        doc = pymupdf.open(str(pdf_path))
        try:
            page = doc[page_number - 1]
            spans = self.extract_page(page, page_number=page_number)
            plain = self.extract_plain_text(page)
            return spans, plain
        finally:
            doc.close()
