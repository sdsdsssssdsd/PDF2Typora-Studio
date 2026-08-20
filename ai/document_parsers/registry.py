"""Registry of document parser providers (Phase 9.5.2)."""

from __future__ import annotations

from ai.document_parsers.base import DocumentParserProvider
from ai.document_parsers.chandra_provider import ChandraProvider
from ai.document_parsers.docling_provider import DoclingProvider
from ai.document_parsers.marker_provider import MarkerProvider
from ai.document_parsers.mineru_provider import MinerUProvider
from ai.document_parsers.native_pdf_provider import NativePdfProvider

# Optional: keep legacy adapters discoverable
ENGINE_ORDER = (
    "native_pdf",
    "mineru",
    "marker",
    "docling",
    "chandra",
)


def all_providers() -> list[DocumentParserProvider]:
    return [
        NativePdfProvider(),
        MinerUProvider(),
        MarkerProvider(),
        DoclingProvider(),
        ChandraProvider(),
    ]


def get_provider(engine_id: str) -> DocumentParserProvider | None:
    for p in all_providers():
        if p.engine_id == engine_id:
            return p
    return None


def list_engines() -> list[dict]:
    rows = []
    for p in all_providers():
        rows.append(
            {
                "id": p.engine_id,
                "name": p.display_name,
                "available": p.available(),
                "license": p.license_note,
            }
        )
    return rows
