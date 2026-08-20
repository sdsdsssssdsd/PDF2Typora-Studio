"""Build PageEvidenceManifest from PDF native text + optional OCR."""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf

from ai.document_parsers.ppocr_adapter import OCRPageResult, PPOCRAdapter
from core.evidence_models import (
    EvidenceBlock,
    PageEvidenceManifest,
    PageTextSourceMode,
)
from core.layout_models import FigureGroup
from services.figure_group_service import FigureGroupService
from services.pdf_text_style_extractor import PDFTextStyleExtractor
from utils.hashing import file_sha256
from utils.logger import get_logger

logger = get_logger("page_evidence_builder")

_REPLACEMENT = "\ufffd"
_CAPTION_RE = re.compile(
    r"(?:Fig\.|Figure|图)\s*([0-9]+[A-Za-z]?)", re.IGNORECASE
)


def _looks_garbled(text: str) -> bool:
    if not text or not text.strip():
        return True
    n = len(text)
    bad = text.count(_REPLACEMENT) + sum(1 for c in text if ord(c) < 9)
    if n < 40:
        return True
    return (bad / max(n, 1)) > 0.08


def choose_text_source_mode(
    *, pdf_text: str, ocr_text: str, ocr_ok: bool
) -> PageTextSourceMode:
    pdf_ok = bool(pdf_text.strip()) and not _looks_garbled(pdf_text)
    if pdf_ok and ocr_ok and ocr_text.strip():
        return PageTextSourceMode.PDF_NATIVE_PLUS_OCR
    if pdf_ok:
        return PageTextSourceMode.PDF_NATIVE
    if ocr_ok and ocr_text.strip():
        return PageTextSourceMode.OCR_PRIMARY
    if ocr_text.strip():
        return PageTextSourceMode.OCR_ONLY
    return PageTextSourceMode.PDF_NATIVE


class PageEvidenceBuilder:
    version = "1"

    def __init__(self) -> None:
        self.text_extractor = PDFTextStyleExtractor()
        self.ocr = PPOCRAdapter()
        self.figure_groups = FigureGroupService()

    def build(
        self,
        *,
        pdf_path: Path,
        page_number: int,
        page_image: Path | None = None,
        pdf_hash: str = "",
        run_ocr: bool = True,
    ) -> PageEvidenceManifest:
        pdf_hash = pdf_hash or (
            file_sha256(pdf_path) if pdf_path.exists() else ""
        )
        warnings: list[str] = []
        blocks: list[EvidenceBlock] = []
        figure_labels: list[str] = []

        spans = []
        pdf_plain = ""
        with pymupdf.open(pdf_path) as doc:
            page = doc[page_number - 1]
            spans = self.text_extractor.extract_page(page, page_number=page_number)
            pdf_plain = self.text_extractor.extract_plain_text(page)

        try:
            captions = self.figure_groups.discover_captions(
                page_number=page_number, spans=spans, plain_text=pdf_plain
            )
            groups = self.figure_groups.build_groups(
                page_number=page_number,
                captions=captions,
                candidates=[],
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("figure group detect failed: %s", exc)
            groups = []
            warnings.append(f"figure_group_detect_failed:{exc}")

        bid = 0
        prev_block = -1
        buf: list[str] = []
        buf_meta: dict = {}

        def flush_paragraph() -> None:
            nonlocal bid, buf, buf_meta
            if not buf:
                return
            text = "".join(buf).strip()
            if not text:
                buf = []
                return
            bid += 1
            size = float(buf_meta.get("size") or 0)
            typ = "heading" if size >= 14 or (buf_meta.get("bold") and len(text) < 80) else "paragraph"
            blocks.append(
                EvidenceBlock(
                    id=f"b{bid:03d}",
                    type=typ,
                    text=text,
                    bbox=buf_meta.get("bbox"),
                    bold=bool(buf_meta.get("bold")),
                    italic=bool(buf_meta.get("italic")),
                    color=str(buf_meta.get("color") or "#000000"),
                    source="pdf",
                )
            )
            buf = []
            buf_meta = {}

        for sp in spans:
            if sp.block_no != prev_block and buf:
                flush_paragraph()
            prev_block = sp.block_no
            if not buf_meta:
                buf_meta = {
                    "bold": sp.bold,
                    "italic": sp.italic,
                    "color": sp.color_hex,
                    "size": sp.size,
                    "bbox": list(sp.bbox_pdf) if sp.bbox_pdf else None,
                }
            else:
                buf_meta["bold"] = buf_meta.get("bold") or sp.bold
            buf.append(sp.text)
        flush_paragraph()

        for g in groups:
            if not isinstance(g, FigureGroup):
                continue
            figure_labels.append(str(g.figure_label))
            bid += 1
            blocks.append(
                EvidenceBlock(
                    id=f"f{bid:03d}",
                    type="figure_group",
                    text=g.caption or f"Fig. {g.figure_label}",
                    bbox=list(g.bbox_pdf) if g.bbox_pdf else None,
                    source="figure",
                    extra={
                        "label": g.figure_label,
                        "subfigures": list(g.subfigures),
                        "group_id": g.ensure_id(),
                        "force_pdf_clip": g.force_pdf_clip,
                    },
                )
            )

        ocr_result = OCRPageResult(ok=False, engine="none", installed=False)
        if run_ocr and page_image is not None and page_image.exists():
            ocr_result = self.ocr.recognize_image(page_image)
            if not ocr_result.installed:
                warnings.append("paddleocr_not_installed")
            elif not ocr_result.ok:
                warnings.append(f"ocr_failed:{ocr_result.error}")
            else:
                for i, line in enumerate(ocr_result.lines, start=1):
                    bid += 1
                    blocks.append(
                        EvidenceBlock(
                            id=f"o{i:03d}",
                            type="ocr_line",
                            text=line.text,
                            bbox=line.bbox,
                            source="ocr",
                            confidence=line.confidence,
                        )
                    )
                    m = _CAPTION_RE.search(line.text)
                    if m and m.group(1) not in figure_labels:
                        figure_labels.append(m.group(1))

        mode = choose_text_source_mode(
            pdf_text=pdf_plain,
            ocr_text=ocr_result.plain_text,
            ocr_ok=ocr_result.ok,
        )
        if mode in {
            PageTextSourceMode.OCR_PRIMARY,
            PageTextSourceMode.OCR_ONLY,
        }:
            warnings.append("pdf_text_layer_weak_or_empty")

        return PageEvidenceManifest(
            page_number=page_number,
            mode=mode,
            pdf_hash=pdf_hash,
            blocks=blocks,
            pdf_plain_text=pdf_plain,
            ocr_plain_text=ocr_result.plain_text,
            figure_labels=figure_labels,
            warnings=warnings,
            meta={
                "ocr_engine": ocr_result.engine,
                "ocr_installed": ocr_result.installed,
                "span_count": len(spans),
                "builder_version": self.version,
            },
        )
