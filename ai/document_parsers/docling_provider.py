"""Docling provider — optional dependency."""

from __future__ import annotations

import time
from pathlib import Path

from ai.document_parsers.base import DocumentParserProvider
from core.document_page_model import (
    BlockSource,
    DocumentBlock,
    DocumentPageEvidence,
)
from utils.logger import get_logger

logger = get_logger("docling_provider")


class DoclingProvider(DocumentParserProvider):
    engine_id = "docling"
    display_name = "Docling"
    license_note = "MIT"

    def available(self) -> bool:
        try:
            import docling  # noqa: F401

            return True
        except Exception:  # noqa: BLE001
            return False

    def analyze_page(
        self,
        pdf_path: Path,
        page_number: int,
        *,
        page_image: Path | None = None,
    ) -> DocumentPageEvidence:
        _ = page_image
        if not self.available():
            return self.unavailable_result(
                page_number, error="docling_not_installed", installed=False
            )
        started = time.perf_counter()
        try:
            from docling.document_converter import DocumentConverter

            converter = DocumentConverter()
            result = converter.convert(str(pdf_path))
            doc = result.document
            md = ""
            if hasattr(doc, "export_to_markdown"):
                md = doc.export_to_markdown() or ""
            blocks: list[DocumentBlock] = []
            labels: list[str] = []
            tables = formulas = 0
            texts: list[str] = []
            # Best-effort iterate texts / pictures if API present
            texts_iter = getattr(doc, "texts", None) or []
            for i, t in enumerate(texts_iter):
                label = str(getattr(t, "label", "") or getattr(t, "text", "") or "")
                text = str(getattr(t, "text", "") or label)
                prov = getattr(t, "prov", None) or []
                page_nos = []
                for p in prov:
                    pn = getattr(p, "page_no", None)
                    if pn is not None:
                        page_nos.append(int(pn))
                if page_nos and (page_number not in page_nos) and (
                    page_number - 1 not in page_nos
                ):
                    continue
                typ = str(getattr(t, "label", "text") or "text").lower()
                mapped = "text"
                if "title" in typ or "section" in typ or "heading" in typ:
                    mapped = "heading"
                elif "caption" in typ:
                    mapped = "caption"
                blocks.append(
                    DocumentBlock(
                        block_id=f"docling_t{i+1}",
                        type=mapped,
                        text=text,
                        reading_order=i,
                        source=BlockSource.PARSER.value,
                        extra={"raw_label": typ},
                    )
                )
                if text.strip():
                    texts.append(text)

            pictures = getattr(doc, "pictures", None) or []
            for i, pic in enumerate(pictures):
                prov = getattr(pic, "prov", None) or []
                page_nos = [
                    int(getattr(p, "page_no", page_number)) for p in prov
                ] or [page_number]
                if page_number not in page_nos and (page_number - 1) not in page_nos:
                    continue
                lab = str(i + 1)
                labels.append(lab)
                blocks.append(
                    DocumentBlock(
                        block_id=f"docling_fig{i+1}",
                        type="figure_group",
                        text="",
                        reading_order=len(blocks),
                        source=BlockSource.LAYOUT_ENGINE.value,
                        extra={"label": lab},
                    )
                )

            tables_iter = getattr(doc, "tables", None) or []
            tables = len(list(tables_iter))

            return DocumentPageEvidence(
                page_number=page_number,
                engine=self.engine_id,
                blocks=blocks,
                plain_text="\n".join(texts) if texts else md,
                markdown=md,
                figure_labels=labels,
                table_count=tables,
                formula_count=formulas,
                ok=True,
                installed=True,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                provenance={
                    "provider": self.engine_id,
                    "license": self.license_note,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Docling analyze failed: %s", exc)
            ev = self.unavailable_result(
                page_number, error=f"docling_error:{exc}", installed=True
            )
            ev.duration_ms = (time.perf_counter() - started) * 1000.0
            return ev
