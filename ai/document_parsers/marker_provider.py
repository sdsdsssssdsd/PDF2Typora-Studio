"""Marker provider — optional dependency."""

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

logger = get_logger("marker_provider")


class MarkerProvider(DocumentParserProvider):
    engine_id = "marker"
    display_name = "Marker"
    license_note = "Apache-2.0 code; model weights may have separate restrictions"

    def available(self) -> bool:
        for mod in ("marker", "marker_pdf", "marker.converters"):
            try:
                __import__(mod.split(".")[0])
                return True
            except Exception:  # noqa: BLE001
                continue
        try:
            import importlib.util

            return importlib.util.find_spec("marker") is not None
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
                page_number, error="marker_not_installed", installed=False
            )
        started = time.perf_counter()
        try:
            # Marker typically converts whole PDF; extract page markdown if possible.
            from marker.converters.pdf import PdfConverter
            from marker.models import create_model_dict

            converter = PdfConverter(artifact_dict=create_model_dict())
            rendered = converter(str(pdf_path))
            md = getattr(rendered, "markdown", None) or str(rendered)
            # Split pages heuristically when multi-page
            pages = md.split("\n\n")
            page_md = md
            if page_number > 1 and len(pages) >= page_number:
                page_md = pages[page_number - 1]
            blocks = [
                DocumentBlock(
                    block_id="marker_md",
                    type="text",
                    text=page_md,
                    reading_order=0,
                    source=BlockSource.PARSER.value,
                )
            ]
            return DocumentPageEvidence(
                page_number=page_number,
                engine=self.engine_id,
                blocks=blocks,
                plain_text=page_md,
                markdown=page_md,
                ok=True,
                installed=True,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                warnings=["marker_full_pdf_convert_page_split_heuristic"],
                provenance={
                    "provider": self.engine_id,
                    "license": self.license_note,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Marker analyze failed: %s", exc)
            ev = self.unavailable_result(
                page_number,
                error=f"marker_error:{exc}",
                installed=True,
            )
            ev.duration_ms = (time.perf_counter() - started) * 1000.0
            return ev
