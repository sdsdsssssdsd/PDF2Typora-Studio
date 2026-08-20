"""Plan and execute figure extraction (native / PDF clip)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf

from core.figure_models import (
    FIGURE_PIPELINE_VERSION,
    FigureArtifactResult,
    FigureCandidate,
    FigureExtractionPlan,
    FigureMatch,
    FigureRequest,
    FigureSourceMethod,
)
from utils.geometry import bbox_1000_to_page_rect, clamp_rect, expand_rect
from utils.hashing import figure_artifact_hash
from utils.logger import get_logger
from utils.paths import ensure_dir

logger = get_logger("figure_extractor")


class FigureExtractionPlanner:
    def plan(
        self,
        match: FigureMatch,
        *,
        crop_dpi: int,
        padding_ratio: float,
    ) -> FigureExtractionPlan:
        req = match.request
        ai = req.ai_bbox_1000
        cand = match.candidate

        if not match.auto_resolvable:
            clip = ai
            return FigureExtractionPlan(
                request=req,
                method=FigureSourceMethod.UNRESOLVED,
                clip_rect_pdf=None,
                candidate=cand,
                match_score=match.score,
                crop_dpi=crop_dpi,
                padding_ratio=padding_ratio,
                reasons=list(match.reasons),
            )

        # Phase 9.5: multi-subfigure / mixed / explicit group → always PDF_CLIP
        if getattr(req, "force_pdf_clip", False):
            return FigureExtractionPlan(
                request=req,
                method=FigureSourceMethod.PDF_CLIP,
                clip_rect_pdf=None,
                candidate=cand,
                match_score=match.score,
                crop_dpi=crop_dpi,
                padding_ratio=padding_ratio,
                reasons=["figure_group_requires_pdf_clip"],
            )

        if cand and cand.candidate_type == "raster" and cand.xref and cand.xref > 0:
            if not cand.metadata.get("has_mask") and match.score >= 0.85:
                if match.containment and match.containment >= 0.6:
                    return FigureExtractionPlan(
                        request=req,
                        method=FigureSourceMethod.PDF_NATIVE,
                        clip_rect_pdf=cand.bbox_pdf,
                        candidate=cand,
                        match_score=match.score,
                        crop_dpi=crop_dpi,
                        padding_ratio=padding_ratio,
                        reasons=["native_high_match"],
                    )

        return FigureExtractionPlan(
            request=req,
            method=FigureSourceMethod.PDF_CLIP,
            clip_rect_pdf=None,
            candidate=cand,
            match_score=match.score,
            crop_dpi=crop_dpi,
            padding_ratio=padding_ratio,
            reasons=["pdf_clip"],
        )


class FigureExtractor:
    def __init__(
        self,
        *,
        figures_dir: Path,
        pdf_hash: str,
        cfg: dict[str, Any] | None = None,
    ) -> None:
        self.figures_dir = ensure_dir(figures_dir)
        self.pdf_hash = pdf_hash
        cfg = cfg or {}
        self.use_cache = bool(cfg.get("use_cache", True))
        self.preserve_native = bool(cfg.get("preserve_native_format", True))
        self.pipeline_version = str(cfg.get("pipeline_version", FIGURE_PIPELINE_VERSION))

    def extract(
        self,
        doc: pymupdf.Document,
        page: pymupdf.Page,
        plan: FigureExtractionPlan,
        *,
        resolved_bbox_1000: tuple[int, int, int, int] | None = None,
        force: bool = False,
    ) -> FigureArtifactResult:
        req = plan.request
        bbox = (
            resolved_bbox_1000
            or getattr(req, "group_bbox_1000", None)
            or req.ai_bbox_1000
            or (plan.candidate.bbox_1000 if plan.candidate else None)
        )
        if bbox is None and plan.method != FigureSourceMethod.PDF_NATIVE:
            return FigureArtifactResult(
                page_number=req.page_number,
                figure_index=req.figure_index,
                artifact_path=None,
                artifact_hash="",
                source_method=FigureSourceMethod.UNRESOLVED,
                valid=False,
                errors=["invalid_ai_bbox"],
            )

        clip_pdf: tuple[float, float, float, float] | None = None
        if bbox is not None:
            rect = bbox_1000_to_page_rect(bbox, page)
            pad_x = page.rect.width * plan.padding_ratio
            pad_y = page.rect.height * plan.padding_ratio
            rect = clamp_rect(expand_rect(rect, pad_x, pad_y), page.rect)
            clip_pdf = (rect.x0, rect.y0, rect.x1, rect.y1)

        art_hash = figure_artifact_hash(
            pdf_hash=self.pdf_hash,
            page_number=req.page_number,
            figure_index=req.figure_index,
            source_method=plan.method.value,
            xref=plan.candidate.xref if plan.candidate else None,
            digest=plan.candidate.digest if plan.candidate else None,
            crop_bbox=clip_pdf,
            crop_dpi=plan.crop_dpi,
            padding_ratio=plan.padding_ratio,
            pipeline_version=self.pipeline_version,
        )

        ext = self._extension(plan)
        out_name = f"p{req.page_number:04d}_fig{req.figure_index:02d}{ext}"
        final_path = self.figures_dir / out_name

        if (
            self.use_cache
            and not force
            and final_path.exists()
            and final_path.stat().st_size > 0
        ):
            w, h = self._image_size(final_path)
            return FigureArtifactResult(
                page_number=req.page_number,
                figure_index=req.figure_index,
                artifact_path=str(final_path),
                artifact_hash=art_hash,
                source_method=plan.method,
                width=w,
                height=h,
                cached=True,
                valid=True,
            )

        tmp = final_path.with_suffix(final_path.suffix + ".part")
        try:
            if plan.method == FigureSourceMethod.PDF_NATIVE and plan.candidate and plan.candidate.xref:
                self._extract_native(doc, plan.candidate.xref, tmp, ext)
            elif plan.method in {
                FigureSourceMethod.PDF_CLIP,
                FigureSourceMethod.MANUAL_CROP,
                FigureSourceMethod.PAGE_RASTER_CROP,
            } and clip_pdf is not None:
                self._pdf_clip(page, clip_pdf, plan.crop_dpi, tmp)
            else:
                return FigureArtifactResult(
                    page_number=req.page_number,
                    figure_index=req.figure_index,
                    artifact_path=None,
                    artifact_hash=art_hash,
                    source_method=plan.method,
                    valid=False,
                    errors=["unresolved"],
                )
        except Exception as exc:  # noqa: BLE001
            tmp.unlink(missing_ok=True)
            return FigureArtifactResult(
                page_number=req.page_number,
                figure_index=req.figure_index,
                artifact_path=None,
                artifact_hash=art_hash,
                source_method=plan.method,
                valid=False,
                errors=[f"extract_failed:{exc}"],
            )

        if not tmp.exists() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            return FigureArtifactResult(
                page_number=req.page_number,
                figure_index=req.figure_index,
                artifact_path=None,
                artifact_hash=art_hash,
                source_method=plan.method,
                valid=False,
                errors=["artifact_invalid"],
            )

        staged = final_path.with_suffix(final_path.suffix + ".tmp")
        if staged.exists():
            staged.unlink()
        tmp.replace(staged)
        staged.replace(final_path)

        w, h = self._image_size(final_path)
        return FigureArtifactResult(
            page_number=req.page_number,
            figure_index=req.figure_index,
            artifact_path=str(final_path),
            artifact_hash=art_hash,
            source_method=plan.method,
            width=w,
            height=h,
            cached=False,
            valid=True,
        )

    def extract_to_path(
        self,
        doc: pymupdf.Document,
        page: pymupdf.Page,
        plan: FigureExtractionPlan,
        *,
        dest_path: Path,
        resolved_bbox_1000: tuple[int, int, int, int] | None = None,
        force: bool = True,
    ) -> FigureArtifactResult:
        """Write preview artifact to dest_path (never figures/)."""
        req = plan.request
        bbox = resolved_bbox_1000 or req.ai_bbox_1000 or (
            plan.candidate.bbox_1000 if plan.candidate else None
        )
        if bbox is None:
            return FigureArtifactResult(
                page_number=req.page_number,
                figure_index=req.figure_index,
                artifact_path=None,
                artifact_hash="",
                source_method=plan.method,
                valid=False,
                errors=["invalid_ai_bbox"],
            )
        rect = bbox_1000_to_page_rect(bbox, page)
        pad_x = page.rect.width * plan.padding_ratio
        pad_y = page.rect.height * plan.padding_ratio
        rect = clamp_rect(expand_rect(rect, pad_x, pad_y), page.rect)
        clip_pdf = (rect.x0, rect.y0, rect.x1, rect.y1)
        art_hash = figure_artifact_hash(
            pdf_hash=self.pdf_hash,
            page_number=req.page_number,
            figure_index=req.figure_index,
            source_method=FigureSourceMethod.PDF_CLIP.value,
            xref=None,
            digest=None,
            crop_bbox=clip_pdf,
            crop_dpi=plan.crop_dpi,
            padding_ratio=plan.padding_ratio,
            pipeline_version=self.pipeline_version,
        )
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._pdf_clip(page, clip_pdf, plan.crop_dpi, dest_path)
        except Exception as exc:  # noqa: BLE001
            return FigureArtifactResult(
                page_number=req.page_number,
                figure_index=req.figure_index,
                artifact_path=None,
                artifact_hash=art_hash,
                source_method=FigureSourceMethod.PDF_CLIP,
                valid=False,
                errors=[f"preview_failed:{exc}"],
            )
        if not dest_path.exists() or dest_path.stat().st_size == 0:
            return FigureArtifactResult(
                page_number=req.page_number,
                figure_index=req.figure_index,
                artifact_path=None,
                artifact_hash=art_hash,
                source_method=FigureSourceMethod.PDF_CLIP,
                valid=False,
                errors=["artifact_invalid"],
            )
        w, h = self._image_size(dest_path)
        return FigureArtifactResult(
            page_number=req.page_number,
            figure_index=req.figure_index,
            artifact_path=str(dest_path),
            artifact_hash=art_hash,
            source_method=FigureSourceMethod.PDF_CLIP,
            width=w,
            height=h,
            cached=False,
            valid=True,
        )

    def _extension(self, plan: FigureExtractionPlan) -> str:
        if plan.method == FigureSourceMethod.PDF_NATIVE and self.preserve_native:
            return ".jpg"
        return ".png"

    @staticmethod
    def _extract_native(
        doc: pymupdf.Document, xref: int, out: Path, ext: str
    ) -> None:
        img = doc.extract_image(xref)
        out.write_bytes(img["image"])

    @staticmethod
    def _pdf_clip(
        page: pymupdf.Page,
        clip_pdf: tuple[float, float, float, float],
        dpi: int,
        out: Path,
    ) -> None:
        rect = pymupdf.Rect(clip_pdf)
        zoom = dpi / 72.0
        mat = pymupdf.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, clip=rect, alpha=False, colorspace=pymupdf.csRGB)
        try:
            pix.save(str(out), output="png")
        finally:
            pix = None

    @staticmethod
    def _image_size(path: Path) -> tuple[int | None, int | None]:
        try:
            import pymupdf

            pix = pymupdf.Pixmap(str(path))
            w, h = pix.width, pix.height
            pix = None
            return w, h
        except Exception:  # noqa: BLE001
            return None, None
