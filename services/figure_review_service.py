"""Figure review actions — preview, accept, placement (Phase 6.5)."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymupdf

from ai.schemas.transcription import PageTranscriptionResult
from config.config_manager import load_config
from core.figure_models import (
    FigureExtractionPlan,
    FigureRequest,
    FigureSourceMethod,
    FigureStatus,
)
from core.models import PipelineStage, StageStatus
from services.figure_artifact_validator import FigureArtifactValidator
from services.figure_candidate_service import FigureCandidateService
from services.figure_extractor import FigureExtractionPlanner, FigureExtractor
from services.figure_marker_normalizer import canonical_marker
from services.figure_marker_validator import FigureMarkerValidator
from services.figure_matcher import FigureMatcher
from services.resolved_page_builder import (
    ManualMarkerPlacement,
    MarkerRepairRecord,
    ResolvedPageBuilder,
    ResolvedPageInput,
)
from storage.database import Database
from storage.repository import ProjectRepository
from utils.hashing import file_sha256
from utils.logger import get_logger
from utils.paths import ensure_dir

logger = get_logger("figure_review_service")

MIN_BBOX_AREA = 400  # ~20x20 in 1000 space


def validate_bbox_1000(bbox: tuple[int, int, int, int]) -> list[str]:
    x0, y0, x1, y1 = bbox
    errs: list[str] = []
    if x0 >= x1 or y0 >= y1:
        errs.append("invalid_bbox_order")
    for v in bbox:
        if v < 0 or v > 1000:
            errs.append("bbox_out_of_range")
    if (x1 - x0) * (y1 - y0) < MIN_BBOX_AREA:
        errs.append("bbox_too_small")
    return errs


class FigureReviewService:
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
        self.pdf_hash = pdf_hash or file_sha256(pdf_path)
        self.config = config or load_config()
        fig_cfg = self.config.get("figures") or {}
        norm_cfg = fig_cfg.get("marker_normalization") or {}
        self.marker_validator = FigureMarkerValidator(
            allow_safe_syntax_repair=bool(
                norm_cfg.get("allow_safe_syntax_repair", True)
            )
        )
        self.candidate_svc = FigureCandidateService(fig_cfg)
        self.matcher = FigureMatcher(fig_cfg)
        self.planner = FigureExtractionPlanner()
        self.extractor = FigureExtractor(
            figures_dir=project_root / "figures",
            pdf_hash=self.pdf_hash,
            cfg=fig_cfg,
        )
        self.artifact_validator = FigureArtifactValidator(fig_cfg)
        self.builder = ResolvedPageBuilder(project_root / "resolved_pages")
        preview_dpi = int((fig_cfg.get("preview") or {}).get("dpi", 200))
        self._preview_root = ensure_dir(
            project_root / ".cache" / "figure_preview"
        )
        self._preview_dpi = preview_dpi

    def _repo(self) -> tuple[Database, ProjectRepository]:
        db = Database(self.db_path)
        db.initialize()
        return db, ProjectRepository(db)

    def load_context(self, page_number: int, figure_index: int) -> dict[str, Any]:
        canon_md = (
            self.project_root / "markdown_pages" / f"page_{page_number:04d}.md"
        ).read_text(encoding="utf-8")
        payload = json.loads(
            (
                self.project_root / "page_results" / f"page_{page_number:04d}.json"
            ).read_text(encoding="utf-8")
        )
        result = PageTranscriptionResult.model_validate(payload["result"])
        requests = [
            FigureRequest(
                page_number=page_number,
                figure_index=f.figure_index,
                marker=f.marker or "",
                figure_type=f.figure_type,
                caption=f.caption,
                ai_bbox_1000=f.bbox_1000,
            )
            for f in result.figures
        ]
        marker_report = self.marker_validator.validate(
            page_number=page_number, markdown=canon_md, requests=requests
        )
        doc = pymupdf.open(str(self.pdf_path))
        try:
            page = doc[page_number - 1]
            candidates = self.candidate_svc.discover(page, page_number)
        finally:
            doc.close()

        db, repo = self._repo()
        try:
            rows = repo.list_figures(page_number=page_number)
            row = next(
                (r for r in rows if int(r["figure_index"]) == figure_index), {}
            )
        finally:
            db.close()

        req = next((r for r in requests if r.figure_index == figure_index), None)
        matches = []
        if req:
            marker_ok = marker_report.ok or marker_report.safe_marker_fix
            m = self.matcher.match(req, candidates, marker_ok=marker_ok)
            matches = [{"candidate": c, "match": m} for c in candidates[:12]]

        page_png = self.project_root / "pages" / f"page_{page_number:04d}.png"
        return {
            "page_number": page_number,
            "figure_index": figure_index,
            "canonical_md": canon_md,
            "marker_report": marker_report,
            "figure_row": row,
            "request": req,
            "candidates": candidates,
            "page_image": str(page_png) if page_png.exists() else None,
            "issues": marker_report.issues,
        }

    def generate_preview(
        self,
        *,
        page_number: int,
        figure_index: int,
        bbox_1000: tuple[int, int, int, int],
        candidate_id: str | None = None,
    ) -> Path:
        errs = validate_bbox_1000(bbox_1000)
        if errs:
            raise ValueError(";".join(errs))

        req = self._figure_request(page_number, figure_index)
        doc = pymupdf.open(str(self.pdf_path))
        try:
            page = doc[page_number - 1]
            candidates = self.candidate_svc.discover(page, page_number)
            candidate = next(
                (c for c in candidates if c.candidate_id == candidate_id), None
            )
            marker_ok = True
            match = self.matcher.match(req, candidates, marker_ok=marker_ok)
            if candidate:
                match.candidate = candidate
                match.score = 1.0
            plan = self.planner.plan(
                match,
                crop_dpi=self._preview_dpi,
                padding_ratio=float(
                    (self.config.get("figures") or {}).get(
                        "crop_padding_page_ratio", 0.008
                    )
                ),
            )
            plan.method = FigureSourceMethod.PDF_CLIP
            out_dir = ensure_dir(
                self._preview_root / f"p{page_number:04d}_fig{figure_index:02d}"
            )
            for old in out_dir.glob("*"):
                old.unlink()
            preview_path = out_dir / "preview.png"
            art = self.extractor.extract_to_path(
                doc,
                page,
                plan,
                dest_path=preview_path,
                resolved_bbox_1000=bbox_1000,
                force=True,
            )
            if not art.valid or not preview_path.exists():
                raise RuntimeError(
                    ";".join(art.errors) or "preview generation failed"
                )
            return preview_path
        finally:
            doc.close()

    def accept_figure(
        self,
        *,
        page_number: int,
        figure_index: int,
        bbox_1000: tuple[int, int, int, int],
        candidate_id: str | None = None,
        review_action: str = "manual_accept",
    ) -> dict[str, Any]:
        errs = validate_bbox_1000(bbox_1000)
        if errs:
            raise ValueError(";".join(errs))

        req = self._figure_request(page_number, figure_index)
        doc = pymupdf.open(str(self.pdf_path))
        try:
            page = doc[page_number - 1]
            candidates = self.candidate_svc.discover(page, page_number)
            candidate = next(
                (c for c in candidates if c.candidate_id == candidate_id), None
            )
            match = self.matcher.match(req, candidates, marker_ok=True)
            if candidate:
                match.candidate = candidate
            plan = self.planner.plan(
                match,
                crop_dpi=int((self.config.get("figures") or {}).get("crop_dpi", 300)),
                padding_ratio=float(
                    (self.config.get("figures") or {}).get(
                        "crop_padding_page_ratio", 0.008
                    )
                ),
            )
            plan.method = FigureSourceMethod.PDF_CLIP
            art = self.extractor.extract(
                doc, page, plan, resolved_bbox_1000=bbox_1000, force=True
            )
            art = self.artifact_validator.validate(
                art,
                page_width=float(page.rect.width),
                page_height=float(page.rect.height),
            )
        finally:
            doc.close()

        if not art.valid or not art.artifact_path:
            raise RuntimeError(";".join(art.errors) or "extract failed")

        now = datetime.now(timezone.utc).isoformat()
        # Preserve prior marker-placement provenance across accept
        db, repo = self._repo()
        try:
            existing_rows = repo.list_figures(page_number=page_number)
            existing = next(
                (
                    r
                    for r in existing_rows
                    if int(r["figure_index"]) == figure_index
                ),
                {},
            )
        finally:
            db.close()

        row = {
            "page_number": page_number,
            "figure_index": figure_index,
            "status": FigureStatus.RESOLVED.value,
            "artifact_path": art.artifact_path,
            "artifact_hash": art.artifact_hash,
            "source_method": plan.method.value,
            "match_score": match.score,
            "auto_resolved": False,
            "manually_adjusted": True,
            "manual_bbox_1000": bbox_1000,
            "resolved_bbox_1000": bbox_1000,
            "selected_candidate_id": candidate_id,
            "review_status": "accepted",
            "review_action": review_action,
            "reviewed_at": now,
            "warnings": art.warnings,
            "errors": art.errors,
            "manual_marker_offset": existing.get("manual_marker_offset"),
            "manual_marker_before_context": existing.get(
                "manual_marker_before_context"
            ),
            "manual_marker_after_context": existing.get(
                "manual_marker_after_context"
            ),
            "manually_inserted_marker": bool(
                existing.get("manually_inserted_marker")
            ),
            "manual_marker_reassociation": bool(
                existing.get("manual_marker_reassociation")
            ),
            "marker_md_index": existing.get("marker_md_index"),
            "marker_original": existing.get("marker_original"),
            "marker_normalized": existing.get("marker_normalized"),
            "marker_repair_type": existing.get("marker_repair_type"),
            "marker_repaired": bool(existing.get("marker_repaired")),
        }
        self._persist_figure(row)
        self.rebuild_resolved_page(page_number)
        return row

    def confirm_marker_placement(
        self,
        *,
        page_number: int,
        figure_index: int,
        char_offset: int,
        before_context: str,
        after_context: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        db, repo = self._repo()
        try:
            repo.upsert_figure(
                page_number=page_number,
                figure_index=figure_index,
                status=FigureStatus.NEEDS_REVIEW.value,
                manual_marker_offset=char_offset,
                manual_marker_before_context=before_context,
                manual_marker_after_context=after_context,
                manually_inserted_marker=True,
                review_action="manual_marker_insert",
                reviewed_at=now,
            )
        finally:
            db.close()

    def reassociate_marker(
        self,
        *,
        page_number: int,
        figure_index: int,
        marker_md_index: int,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        db, repo = self._repo()
        try:
            repo.upsert_figure(
                page_number=page_number,
                figure_index=figure_index,
                status=FigureStatus.NEEDS_REVIEW.value,
                marker_md_index=marker_md_index,
                manual_marker_reassociation=True,
                review_action="manual_marker_reassociation",
                reviewed_at=now,
            )
        finally:
            db.close()

    def skip_figure(self, *, page_number: int, figure_index: int) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        row = {
            "page_number": page_number,
            "figure_index": figure_index,
            "status": FigureStatus.SKIPPED.value,
            "review_status": "skipped",
            "review_action": "skip",
            "reviewed_at": now,
        }
        self._persist_figure(row)
        self.rebuild_resolved_page(page_number)
        return row

    def not_a_figure(self, *, page_number: int, figure_index: int) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        row = {
            "page_number": page_number,
            "figure_index": figure_index,
            "status": FigureStatus.SKIPPED.value,
            "review_status": "not_a_figure",
            "review_action": "not_a_figure",
            "manually_removed_marker": True,
            "reviewed_at": now,
        }
        self._persist_figure(row)
        self.rebuild_resolved_page(page_number)
        return row

    def rebuild_resolved_page(self, page_number: int) -> str | None:
        canon_path = self.project_root / "markdown_pages" / f"page_{page_number:04d}.md"
        if not canon_path.exists():
            return None
        canonical_md = canon_path.read_text(encoding="utf-8")

        db, repo = self._repo()
        try:
            figures = repo.list_figures(page_number=page_number)
        finally:
            db.close()

        if not figures:
            out = self.builder.copy_canonical(page_number, canonical_md)
            return str(out)

        marker_repairs: list[MarkerRepairRecord] = []
        manual_placements: list[ManualMarkerPlacement] = []
        reassociations: dict[int, int] = {}
        figure_paths: dict[int, str] = {}
        figure_hashes: dict[int, str] = {}
        skip_indices: set[int] = set()

        for fig in figures:
            idx = int(fig["figure_index"])
            st = fig.get("status")
            if st == FigureStatus.SKIPPED.value:
                skip_indices.add(idx)
                continue
            if st not in {
                FigureStatus.RESOLVED.value,
                FigureStatus.CACHED.value,
            }:
                continue
            path = fig.get("file_path") or fig.get("artifact_path")
            if path:
                figure_paths[idx] = path
                figure_hashes[idx] = fig.get("artifact_hash") or file_sha256(
                    Path(path)
                )
            if fig.get("marker_repaired"):
                marker_repairs.append(
                    MarkerRepairRecord(
                        figure_index=idx,
                        original=fig.get("marker_original") or "",
                        normalized=fig.get("marker_normalized")
                        or canonical_marker(page_number, idx),
                        repair_type=fig.get("marker_repair_type") or "syntax_only",
                    )
                )
            if fig.get("manually_inserted_marker") and fig.get("manual_marker_offset") is not None:
                manual_placements.append(
                    ManualMarkerPlacement(
                        figure_index=idx,
                        page_number=page_number,
                        char_offset=int(fig["manual_marker_offset"]),
                        before_context=fig.get("manual_marker_before_context") or "",
                        after_context=fig.get("manual_marker_after_context") or "",
                    )
                )
            if fig.get("manual_marker_reassociation") and fig.get("marker_md_index") is not None:
                reassociations[int(fig["marker_md_index"])] = idx

        if not figure_paths and not skip_indices and not manual_placements:
            return str(self.builder.copy_canonical(page_number, canonical_md))

        md, _ = self.builder.build(
            ResolvedPageInput(
                page_number=page_number,
                canonical_md=canonical_md,
                marker_repairs=marker_repairs,
                manual_placements=manual_placements,
                marker_reassociations=reassociations,
                figure_paths=figure_paths,
                figure_hashes=figure_hashes,
                skip_indices=skip_indices,
            )
        )
        out = self.builder.write_resolved(page_number, md, force=True)
        self._update_page_stage(page_number, figures)
        return str(out)

    def _update_page_stage(
        self, page_number: int, figures: list[dict[str, Any]]
    ) -> None:
        if not figures:
            return
        terminal = {
            FigureStatus.RESOLVED.value,
            FigureStatus.CACHED.value,
            FigureStatus.SKIPPED.value,
        }
        all_done = all(f.get("status") in terminal for f in figures)
        any_fail = any(f.get("status") == FigureStatus.FAILED.value for f in figures)
        any_review = any(
            f.get("status") == FigureStatus.NEEDS_REVIEW.value for f in figures
        )
        if all_done and not any_review:
            status = StageStatus.SUCCESS
        elif any_fail:
            status = StageStatus.FAILED
        else:
            status = StageStatus.NEEDS_REVIEW

        db, repo = self._repo()
        try:
            resolved = self.project_root / "resolved_pages" / f"page_{page_number:04d}.md"
            repo.upsert_stage_state(
                page_number,
                PipelineStage.FIGURES,
                status,
                artifact_path=str(resolved) if resolved.exists() else None,
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
        finally:
            db.close()

    def _figure_request(self, page_number: int, figure_index: int) -> FigureRequest:
        payload = json.loads(
            (
                self.project_root / "page_results" / f"page_{page_number:04d}.json"
            ).read_text(encoding="utf-8")
        )
        result = PageTranscriptionResult.model_validate(payload["result"])
        for f in result.figures:
            if f.figure_index == figure_index:
                return FigureRequest(
                    page_number=page_number,
                    figure_index=figure_index,
                    marker=f.marker or "",
                    figure_type=f.figure_type,
                    caption=f.caption,
                    ai_bbox_1000=f.bbox_1000,
                )
        raise ValueError(f"figure {figure_index} not in json")

    def _persist_figure(self, row: dict[str, Any]) -> None:
        db, repo = self._repo()
        try:
            repo.upsert_figure(
                page_number=int(row["page_number"]),
                figure_index=int(row["figure_index"]),
                status=str(row["status"]),
                file_path=row.get("artifact_path"),
                ai_bbox_1000=row.get("ai_bbox_1000"),
                matched_bbox_1000=row.get("matched_bbox_1000"),
                resolved_bbox_1000=row.get("resolved_bbox_1000"),
                manual_bbox_1000=row.get("manual_bbox_1000"),
                source_method=row.get("source_method"),
                artifact_hash=row.get("artifact_hash"),
                match_score=row.get("match_score"),
                auto_resolved=bool(row.get("auto_resolved")),
                manually_adjusted=bool(row.get("manually_adjusted")),
                marker_original=row.get("marker_original"),
                marker_normalized=row.get("marker_normalized"),
                marker_repair_type=row.get("marker_repair_type"),
                marker_repaired=bool(row.get("marker_repaired")),
                selected_candidate_id=row.get("selected_candidate_id"),
                review_status=row.get("review_status"),
                review_action=row.get("review_action"),
                reviewed_at=row.get("reviewed_at"),
                manually_removed_marker=bool(row.get("manually_removed_marker")),
                manual_marker_offset=row.get("manual_marker_offset"),
                manual_marker_before_context=row.get("manual_marker_before_context"),
                manual_marker_after_context=row.get("manual_marker_after_context"),
                manually_inserted_marker=bool(row.get("manually_inserted_marker")),
                manual_marker_reassociation=bool(row.get("manual_marker_reassociation")),
                marker_md_index=row.get("marker_md_index"),
                warnings=row.get("warnings"),
                error_message=";".join(row.get("errors") or []),
            )
        finally:
            db.close()

    def clear_preview_cache(self, page_number: int, figure_index: int) -> None:
        d = self._preview_root / f"p{page_number:04d}_fig{figure_index:02d}"
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
