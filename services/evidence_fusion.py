"""Convert DocumentPageEvidence → PageEvidenceManifest for DeepSeek fusion."""

from __future__ import annotations

from core.document_page_model import DocumentPageEvidence
from core.evidence_models import (
    EvidenceBlock,
    PageEvidenceManifest,
    PageTextSourceMode,
)


def document_page_to_manifest(
    page: DocumentPageEvidence,
    *,
    pdf_hash: str = "",
) -> PageEvidenceManifest:
    blocks: list[EvidenceBlock] = []
    for b in page.blocks:
        source = "pdf"
        if b.source in {"OCR"}:
            source = "ocr"
        elif b.source in {"LAYOUT_ENGINE", "PARSER", "VLM"}:
            source = "layout"
        blocks.append(
            EvidenceBlock(
                id=b.block_id,
                type=b.type if b.type != "text" else "paragraph",
                text=b.text,
                bbox=b.bbox,
                bold=bool((b.style or {}).get("bold")),
                italic=bool((b.style or {}).get("italic")),
                color=str((b.style or {}).get("color") or "#000000"),
                source=source,
                confidence=b.confidence,
                extra=dict(b.extra),
            )
        )
    mode = PageTextSourceMode.PDF_NATIVE
    if page.engine in {"mineru", "marker", "docling", "chandra"}:
        mode = PageTextSourceMode.PDF_NATIVE_PLUS_OCR
    return PageEvidenceManifest(
        page_number=page.page_number,
        mode=mode,
        pdf_hash=pdf_hash,
        blocks=blocks,
        pdf_plain_text=page.plain_text,
        ocr_plain_text="",
        figure_labels=list(page.figure_labels),
        warnings=list(page.warnings),
        meta={
            "from_engine": page.engine,
            "table_count": page.table_count,
            "formula_count": page.formula_count,
        },
    )
