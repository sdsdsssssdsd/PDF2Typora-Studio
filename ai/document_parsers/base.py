"""Document parser provider ABC — Phase 9.5.2."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from core.document_page_model import DocumentPageEvidence


class DocumentParserProvider(ABC):
    """Pluggable PDF page analyzer. Do not vendor upstream source trees."""

    engine_id: str = "base"
    display_name: str = "Base"
    license_note: str = ""

    @abstractmethod
    def available(self) -> bool:
        ...

    @abstractmethod
    def analyze_page(
        self,
        pdf_path: Path,
        page_number: int,
        *,
        page_image: Path | None = None,
    ) -> DocumentPageEvidence:
        ...

    def unavailable_result(
        self,
        page_number: int,
        *,
        error: str,
        installed: bool = False,
    ) -> DocumentPageEvidence:
        return DocumentPageEvidence(
            page_number=page_number,
            engine=self.engine_id,
            ok=False,
            error=error,
            installed=installed,
            warnings=[error],
            provenance={"provider": self.engine_id, "license": self.license_note},
        )
