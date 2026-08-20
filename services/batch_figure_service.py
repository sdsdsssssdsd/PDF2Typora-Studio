"""Batch figure extraction pipeline."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from config.config_manager import load_config
from core.models import PipelineStage, StageStatus
from services.figure_service import FigureService
from storage.database import Database
from storage.repository import ProjectRepository
from utils.logger import get_logger
from utils.paths import ensure_dir

logger = get_logger("batch_figure")


class BatchFigureService:
    def __init__(
        self,
        *,
        project_root: Path,
        pdf_path: Path,
        db_path: Path,
        pdf_hash: str = "",
        config: dict[str, Any] | None = None,
    ) -> None:
        self.project_root = project_root
        self.pdf_path = pdf_path
        self.db_path = db_path
        self.config = config or load_config()
        self.figure = FigureService(
            project_root=project_root,
            pdf_path=pdf_path,
            db_path=db_path,
            pdf_hash=pdf_hash,
            config=self.config,
        )

    def eligible_pages(self, pages: list[int]) -> tuple[list[int], int]:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            ok: list[int] = []
            skipped = 0
            for p in pages:
                tr = repo.get_stage_state(p, PipelineStage.TRANSCRIBE)
                st = (tr or {}).get("status")
                if st not in {
                    StageStatus.SUCCESS.value,
                    StageStatus.CACHED.value,
                }:
                    skipped += 1
                    continue
                md = self.project_root / "markdown_pages" / f"page_{p:04d}.md"
                js = self.project_root / "page_results" / f"page_{p:04d}.json"
                if md.exists() and js.exists():
                    ok.append(p)
                else:
                    skipped += 1
            return ok, skipped
        finally:
            db.close()

    def process_pages(
        self,
        pages: list[int],
        *,
        force: bool = False,
        analyze_only: bool = False,
        cancel_check: Callable[[], bool] | None = None,
        on_page: Callable[[Any], None] | None = None,
    ) -> dict[str, Any]:
        queued, skipped = self.eligible_pages(pages)
        summary = {
            "pages_requested": len(pages),
            "pages_processed": 0,
            "skipped": skipped,
            "figures_requested": 0,
            "native_extracted": 0,
            "pdf_clipped": 0,
            "auto_resolved": 0,
            "needs_review": 0,
            "failed": 0,
            "cached": 0,
        }
        for page in queued:
            if cancel_check and cancel_check():
                summary["cancelled"] = True
                break
            result = self.figure.process_page(
                page, force=force, analyze_only=analyze_only, cancel_check=cancel_check
            )
            summary["pages_processed"] += 1
            for fig in result.figures:
                summary["figures_requested"] += 1
                st = fig.get("status")
                sm = fig.get("source_method")
                if sm == "pdf_native":
                    summary["native_extracted"] += 1
                elif sm == "pdf_clip":
                    summary["pdf_clipped"] += 1
                if st in {"resolved", "cached"}:
                    summary["auto_resolved"] += 1
                if st == "cached":
                    summary["cached"] += 1
                elif st == "needs_review":
                    summary["needs_review"] += 1
                elif st == "failed":
                    summary["failed"] += 1
            if on_page:
                on_page(result)
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = ensure_dir(self.project_root / "reports") / f"figure_batch_{run_id}.json"
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        summary["report_path"] = str(out)
        return summary

    def accept_manual(
        self,
        *,
        page_number: int,
        figure_index: int,
        bbox_1000: tuple[int, int, int, int],
    ) -> dict[str, Any]:
        """Re-extract with manual bbox and attempt resolve."""
        from core.figure_models import FigureRequest, FigureExtractionPlan, FigureSourceMethod
        from services.figure_extractor import FigureExtractor
        import pymupdf

        json_path = self.project_root / "page_results" / f"page_{page_number:04d}.json"
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        figs = payload["result"].get("figures") or []
        meta = next((f for f in figs if f["figure_index"] == figure_index), None)
        if meta is None:
            raise ValueError("figure not found in canonical json")

        req = FigureRequest(
            page_number=page_number,
            figure_index=figure_index,
            marker=meta.get("marker", ""),
            figure_type=meta.get("figure_type", "unknown"),
            caption=meta.get("caption"),
            ai_bbox_1000=tuple(meta["bbox_1000"]) if meta.get("bbox_1000") else None,
        )
        plan = FigureExtractionPlan(
            request=req,
            method=FigureSourceMethod.MANUAL_CROP,
            clip_rect_pdf=None,
            candidate=None,
            match_score=1.0,
            crop_dpi=int(self.config.get("figures", {}).get("crop_dpi", 300)),
            padding_ratio=float(
                self.config.get("figures", {}).get("crop_padding_page_ratio", 0.008)
            ),
        )
        doc = pymupdf.open(str(self.pdf_path))
        try:
            page = doc[page_number - 1]
            art = self.figure.extractor.extract(
                doc, page, plan, resolved_bbox_1000=bbox_1000, force=True
            )
        finally:
            doc.close()
        row = {
            "page_number": page_number,
            "figure_index": figure_index,
            "status": "resolved" if art.valid else "needs_review",
            "artifact_path": art.artifact_path,
            "artifact_hash": art.artifact_hash,
            "source_method": FigureSourceMethod.MANUAL_CROP.value,
            "match_score": 1.0,
            "auto_resolved": art.valid,
            "manually_adjusted": True,
            "resolved_bbox_1000": bbox_1000,
            "warnings": art.warnings,
            "errors": art.errors,
        }
        self.figure._persist_figure(row)
        return row
