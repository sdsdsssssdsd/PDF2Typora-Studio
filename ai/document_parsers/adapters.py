"""Optional document parser adapters — graceful if not installed."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DocumentParseResult:
    ok: bool
    engine: str
    markdown: str = ""
    layout: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    installed: bool = False


class PaddleOCRVLAdapter:
    """PaddleOCR-VL document parser adapter (optional dependency)."""

    engine = "paddleocr_vl"

    def available(self) -> bool:
        try:
            import paddleocr  # noqa: F401

            return True
        except Exception:  # noqa: BLE001
            return False

    def parse_pdf_page(
        self, pdf_path: Path, page_number: int, **kwargs: Any
    ) -> DocumentParseResult:
        if not self.available():
            return DocumentParseResult(
                ok=False,
                engine=self.engine,
                installed=False,
                error="paddleocr_not_installed",
            )
        return DocumentParseResult(
            ok=False,
            engine=self.engine,
            installed=True,
            error="paddleocr_vl_integration_pending",
        )


class MinerUAdapter:
    """MinerU PDF→Markdown engine adapter (optional dependency)."""

    engine = "mineru"

    def available(self) -> bool:
        try:
            import magic_pdf  # noqa: F401

            return True
        except Exception:  # noqa: BLE001
            try:
                import mineru  # noqa: F401

                return True
            except Exception:  # noqa: BLE001
                return False

    def parse_pdf(self, pdf_path: Path, **kwargs: Any) -> DocumentParseResult:
        if not self.available():
            return DocumentParseResult(
                ok=False,
                engine=self.engine,
                installed=False,
                error="mineru_not_installed",
            )
        return DocumentParseResult(
            ok=False,
            engine=self.engine,
            installed=True,
            error="mineru_integration_pending",
        )
