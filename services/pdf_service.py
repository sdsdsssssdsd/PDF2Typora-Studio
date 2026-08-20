"""PDF inspection and metadata extraction."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from core.exceptions import PDFError
from core.models import PDFInfo
from utils.logger import get_logger

logger = get_logger("pdf_service")


class PDFService:
    """Read PDF metadata without modifying the source file."""

    def inspect(self, pdf_path: Path) -> PDFInfo:
        if not pdf_path.exists():
            raise PDFError(f"PDF not found: {pdf_path}")
        if pdf_path.suffix.lower() != ".pdf":
            raise PDFError(f"Not a PDF file: {pdf_path}")

        try:
            doc = pymupdf.open(str(pdf_path))
        except Exception as exc:
            raise PDFError(f"Cannot open PDF: {exc}") from exc

        try:
            page_count = doc.page_count
            meta = doc.metadata or {}
            metadata = {k: str(v) for k, v in meta.items() if v}
            file_size = pdf_path.stat().st_size

            logger.info(
                "Inspected PDF %s: %d pages, %d bytes",
                pdf_path.name,
                page_count,
                file_size,
            )

            return PDFInfo(
                file_path=pdf_path.resolve(),
                file_name=pdf_path.name,
                file_size=file_size,
                page_count=page_count,
                metadata=metadata,
            )
        finally:
            doc.close()

    def analyze_page_features(self, pdf_path: Path) -> list[dict[str, object]]:
        """Hybrid analysis stub — full implementation in Phase 3."""
        doc = pymupdf.open(str(pdf_path))
        try:
            features = []
            for i in range(doc.page_count):
                page = doc[i]
                features.append(
                    {
                        "page_number": i + 1,
                        "width": page.rect.width,
                        "height": page.rect.height,
                        "rotation": page.rotation,
                        "has_text": bool(page.get_text().strip()),
                        "image_count": len(page.get_images()),
                    }
                )
            return features
        finally:
            doc.close()
