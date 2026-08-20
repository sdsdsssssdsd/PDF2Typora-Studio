"""Chandra OCR 2 provider — optional; prefer CLI/local runtime when present."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from ai.document_parsers.base import DocumentParserProvider
from core.document_page_model import (
    BlockSource,
    DocumentBlock,
    DocumentPageEvidence,
)
from utils.logger import get_logger

logger = get_logger("chandra_provider")


class ChandraProvider(DocumentParserProvider):
    engine_id = "chandra"
    display_name = "Chandra OCR 2"
    license_note = (
        "Apache-2.0 code; commercial self-hosting may need separate model license"
    )

    def available(self) -> bool:
        # chandra-ocr package imports as `chandra` (datalab); ignore unrelated packages
        try:
            import chandra  # noqa: F401
            from pathlib import Path

            init = Path(getattr(chandra, "__file__", "") or "")
            # datalab chandra-ocr ships model/input helpers; fake PyPI "chandra" does not
            if (init.parent / "model").exists() or (init.parent / "input").exists():
                return True
            if hasattr(chandra, "model") or hasattr(chandra, "input"):
                return True
        except Exception:  # noqa: BLE001
            pass
        if shutil.which("chandra"):
            return True
        return False

    def analyze_page(
        self,
        pdf_path: Path,
        page_number: int,
        *,
        page_image: Path | None = None,
    ) -> DocumentPageEvidence:
        if not self.available():
            return self.unavailable_result(
                page_number, error="chandra_not_installed", installed=False
            )
        started = time.perf_counter()
        # Prefer page image if present
        target = page_image if page_image and page_image.exists() else pdf_path
        try:
            if shutil.which("chandra"):
                out = subprocess.run(
                    [
                        "chandra",
                        str(target),
                        "--page",
                        str(page_number),
                        "--format",
                        "json",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                )
                if out.returncode != 0:
                    raise RuntimeError(out.stderr.strip() or "chandra_cli_failed")
                data = json.loads(out.stdout or "{}")
                return self._from_json(page_number, data, started)
            # Python package path — API varies; mark pending if unknown
            return DocumentPageEvidence(
                page_number=page_number,
                engine=self.engine_id,
                ok=False,
                installed=True,
                error="chandra_python_api_pending",
                warnings=["chandra_installed_but_no_stable_page_api"],
                duration_ms=(time.perf_counter() - started) * 1000.0,
                provenance={
                    "provider": self.engine_id,
                    "license": self.license_note,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Chandra analyze failed: %s", exc)
            ev = self.unavailable_result(
                page_number, error=f"chandra_error:{exc}", installed=True
            )
            ev.duration_ms = (time.perf_counter() - started) * 1000.0
            return ev

    def _from_json(
        self, page_number: int, data: dict, started: float
    ) -> DocumentPageEvidence:
        blocks_raw = data.get("blocks") or data.get("layout") or []
        blocks: list[DocumentBlock] = []
        labels: list[str] = []
        texts: list[str] = []
        tables = formulas = 0
        for i, b in enumerate(blocks_raw):
            if not isinstance(b, dict):
                continue
            typ = str(b.get("type") or b.get("label") or "text").lower()
            text = str(b.get("text") or "")
            mapped = "text"
            if "header" in typ and "page" in typ:
                mapped = "header"
            elif "caption" in typ:
                mapped = "caption"
            elif "table" in typ:
                mapped = "table"
                tables += 1
            elif "equation" in typ or "formula" in typ:
                mapped = "formula"
                formulas += 1
            elif typ in {"image", "figure", "diagram"}:
                mapped = "figure_group"
                labels.append(str(b.get("id") or i + 1))
            elif "section" in typ or "heading" in typ:
                mapped = "heading"
            blocks.append(
                DocumentBlock(
                    block_id=f"chandra_{i+1}",
                    type=mapped,
                    text=text,
                    bbox=b.get("bbox"),
                    reading_order=int(b.get("position") or i),
                    source=BlockSource.VLM.value,
                    extra={"raw_type": typ},
                )
            )
            if text.strip():
                texts.append(text)
        md = str(data.get("markdown") or "")
        return DocumentPageEvidence(
            page_number=page_number,
            engine=self.engine_id,
            blocks=blocks,
            plain_text="\n".join(texts) or md,
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
