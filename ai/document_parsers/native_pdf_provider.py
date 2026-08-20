"""Native PyMuPDF provider — always available baseline."""

from __future__ import annotations

import time
from pathlib import Path

import pymupdf

from ai.document_parsers.base import DocumentParserProvider
from core.document_page_model import (
    BlockSource,
    DocumentBlock,
    DocumentPageEvidence,
)
from core.evidence_models import PageEvidenceManifest
from services.page_evidence_builder import PageEvidenceBuilder


class NativePdfProvider(DocumentParserProvider):
    engine_id = "native_pdf"
    display_name = "PyMuPDF Native"
    license_note = "AGPL-3.0 (PyMuPDF); project uses as runtime dependency"

    def available(self) -> bool:
        return True

    def analyze_page(
        self,
        pdf_path: Path,
        page_number: int,
        *,
        page_image: Path | None = None,
    ) -> DocumentPageEvidence:
        started = time.perf_counter()
        builder = PageEvidenceBuilder()
        manifest = builder.build(
            pdf_path=pdf_path,
            page_number=page_number,
            page_image=page_image,
            run_ocr=False,
        )
        width = height = 0.0
        with pymupdf.open(pdf_path) as doc:
            page = doc[page_number - 1]
            width = float(page.rect.width)
            height = float(page.rect.height)
        evidence = manifest_to_document_page(
            manifest, engine=self.engine_id, width=width, height=height
        )
        evidence.duration_ms = (time.perf_counter() - started) * 1000.0
        evidence.provenance = {
            "provider": self.engine_id,
            "pipeline": "PageEvidenceBuilder",
            "license": self.license_note,
        }
        return evidence


def manifest_to_document_page(
    manifest: PageEvidenceManifest,
    *,
    engine: str,
    width: float = 0.0,
    height: float = 0.0,
) -> DocumentPageEvidence:
    blocks: list[DocumentBlock] = []
    for i, b in enumerate(manifest.blocks):
        source = BlockSource.PDF_NATIVE.value
        if b.source == "ocr":
            source = BlockSource.OCR.value
        elif b.source in {"layout", "figure"}:
            source = BlockSource.LAYOUT_ENGINE.value
        blocks.append(
            DocumentBlock(
                block_id=b.id or f"b{i+1}",
                type=b.type,
                text=b.text or "",
                bbox=list(b.bbox) if b.bbox else None,
                reading_order=i,
                style={
                    "bold": b.bold,
                    "italic": b.italic,
                    "color": b.color,
                },
                source=source,
                confidence=b.confidence,
                extra=dict(b.extra),
            )
        )
    table_count = sum(1 for b in blocks if b.type == "table")
    formula_count = sum(1 for b in blocks if b.type in {"formula", "equation"})
    return DocumentPageEvidence(
        page_number=manifest.page_number,
        engine=engine,
        width=width,
        height=height,
        blocks=blocks,
        plain_text=manifest.pdf_plain_text or "",
        markdown="",
        figure_labels=list(manifest.figure_labels),
        table_count=table_count,
        formula_count=formula_count,
        warnings=list(manifest.warnings),
        ok=True,
        installed=True,
    )
