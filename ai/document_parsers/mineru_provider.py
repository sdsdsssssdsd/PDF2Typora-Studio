"""MinerU provider — optional dependency, no vendored source."""

from __future__ import annotations

import json
import time
from pathlib import Path

from ai.document_parsers.base import DocumentParserProvider
from core.document_page_model import (
    BlockSource,
    DocumentBlock,
    DocumentPageEvidence,
)
from utils.logger import get_logger

logger = get_logger("mineru_provider")


class MinerUProvider(DocumentParserProvider):
    engine_id = "mineru"
    display_name = "MinerU"
    license_note = "Apache-2.0 + MinerU commercial/online terms — check before shipping"

    def available(self) -> bool:
        for mod in ("mineru", "magic_pdf"):
            try:
                __import__(mod)
                return True
            except Exception:  # noqa: BLE001
                continue
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
                page_number, error="mineru_not_installed", installed=False
            )
        started = time.perf_counter()
        try:
            return self._analyze_installed(pdf_path, page_number, started)
        except Exception as exc:  # noqa: BLE001
            logger.warning("MinerU analyze failed: %s", exc)
            ev = self.unavailable_result(
                page_number, error=f"mineru_error:{exc}", installed=True
            )
            ev.duration_ms = (time.perf_counter() - started) * 1000.0
            return ev

    def _analyze_installed(
        self, pdf_path: Path, page_number: int, started: float
    ) -> DocumentPageEvidence:
        # Prefer CLI-style content list if user pre-exported; else attempt API.
        # Full MinerU pipeline is heavy; we probe common entry points.
        try:
            from magic_pdf.data.data_reader_writer import FileBasedDataReader
            from magic_pdf.data.dataset import PymuDocDataset
            from magic_pdf.model.doc_analyze_by_custom_model import doc_analyze

            reader = FileBasedDataReader("")
            bits = reader.read(str(pdf_path))
            ds = PymuDocDataset(bits)
            infer = ds.apply(doc_analyze, ocr=False)
            pipe = infer.pipe_txt_mode(
                FileBasedDataReader(""),  # type: ignore[arg-type]
            )
            content_list = pipe.get_content_list(page_number - 1)
            return self._from_content_list(
                page_number, content_list, started, via="magic_pdf"
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("magic_pdf path failed: %s", exc)

        return DocumentPageEvidence(
            page_number=page_number,
            engine=self.engine_id,
            ok=False,
            installed=True,
            error="mineru_integration_pending_or_api_changed",
            warnings=["mineru_installed_but_page_api_unavailable"],
            duration_ms=(time.perf_counter() - started) * 1000.0,
            provenance={"provider": self.engine_id, "license": self.license_note},
        )

    def _from_content_list(
        self,
        page_number: int,
        content_list: list | dict | str,
        started: float,
        *,
        via: str,
    ) -> DocumentPageEvidence:
        if isinstance(content_list, str):
            try:
                content_list = json.loads(content_list)
            except json.JSONDecodeError:
                content_list = []
        items = content_list if isinstance(content_list, list) else []
        blocks: list[DocumentBlock] = []
        labels: list[str] = []
        parts: list[str] = []
        tables = formulas = 0
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            typ = str(item.get("type") or item.get("category_id") or "text").lower()
            text = str(item.get("text") or item.get("content") or "")
            mapped = "text"
            if "title" in typ or "heading" in typ:
                mapped = "heading"
            elif "table" in typ:
                mapped = "table"
                tables += 1
            elif "formula" in typ or "equation" in typ:
                mapped = "formula"
                formulas += 1
            elif "figure" in typ or "image" in typ or "chart" in typ:
                mapped = "figure_group"
                lab = str(item.get("label") or item.get("img_caption") or i + 1)
                labels.append(str(lab))
            elif "caption" in typ:
                mapped = "caption"
            blocks.append(
                DocumentBlock(
                    block_id=f"mineru_{i+1}",
                    type=mapped,
                    text=text,
                    bbox=item.get("bbox"),
                    reading_order=i,
                    source=BlockSource.LAYOUT_ENGINE.value,
                    extra={"raw_type": typ},
                )
            )
            if text.strip():
                parts.append(text)
        return DocumentPageEvidence(
            page_number=page_number,
            engine=self.engine_id,
            blocks=blocks,
            plain_text="\n".join(parts),
            figure_labels=labels,
            table_count=tables,
            formula_count=formulas,
            ok=True,
            installed=True,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            provenance={
                "provider": self.engine_id,
                "via": via,
                "license": self.license_note,
            },
        )
