"""Build PageLayoutManifest before Vision / Figure fusion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pymupdf

from core.layout_models import PageLayoutManifest
from services.figure_candidate_service import FigureCandidateService
from services.figure_group_service import FigureGroupService
from services.pdf_text_style_extractor import PDFTextStyleExtractor
from utils.hashing import file_sha256
from utils.logger import get_logger
from utils.paths import ensure_dir

logger = get_logger("page_layout_manifest")


class PageLayoutManifestService:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        fig_cfg = self.config.get("figures") or {}
        self.text = PDFTextStyleExtractor()
        self.candidates = FigureCandidateService(fig_cfg)
        self.groups = FigureGroupService()

    def build_page(
        self,
        *,
        pdf_path: Path,
        page_number: int,
        pdf_hash: str = "",
        ai_requests: list[Any] | None = None,
    ) -> PageLayoutManifest:
        doc = pymupdf.open(str(pdf_path))
        try:
            page = doc[page_number - 1]
            spans = self.text.extract_page(page, page_number=page_number)
            plain = self.text.extract_plain_text(page)
            cands = self.candidates.discover(page, page_number)
            captions = self.groups.discover_captions(
                page_number=page_number, spans=spans, plain_text=plain
            )
            groups = self.groups.build_groups(
                page_number=page_number,
                captions=captions,
                candidates=cands,
                ai_requests=ai_requests,
                page_width=float(page.rect.width),
                page_height=float(page.rect.height),
            )
            images = [
                {
                    "candidate_id": c.candidate_id,
                    "type": c.candidate_type,
                    "bbox_pdf": c.bbox_pdf,
                    "bbox_1000": c.bbox_1000,
                    "xref": c.xref,
                    "digest": c.digest,
                    "metadata": c.metadata,
                }
                for c in cands
                if c.candidate_type == "raster"
            ]
            vectors = [
                {
                    "candidate_id": c.candidate_id,
                    "type": c.candidate_type,
                    "bbox_pdf": c.bbox_pdf,
                    "bbox_1000": c.bbox_1000,
                    "metadata": c.metadata,
                }
                for c in cands
                if c.candidate_type == "vector"
            ]
            return PageLayoutManifest(
                page_number=page_number,
                pdf_hash=pdf_hash or file_sha256(pdf_path),
                spans=spans,
                captions=captions,
                image_candidates=images,
                vector_candidates=vectors,
                figure_groups=groups,
                plain_text=plain,
            )
        finally:
            doc.close()

    def write(
        self, project_root: Path, manifest: PageLayoutManifest
    ) -> Path:
        out_dir = ensure_dir(project_root / "layout")
        path = out_dir / f"page_{manifest.page_number:04d}.json"
        path.write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path
