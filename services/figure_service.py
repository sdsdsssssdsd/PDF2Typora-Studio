"""Figure pipeline orchestrator — single page."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pymupdf

from ai.schemas.transcription import PageTranscriptionResult
from core.figure_models import (
    FIGURE_PIPELINE_VERSION,
    FigureExtractionPlan,
    FigureMatch,
    FigureRequest,
    FigureSourceMethod,
    FigureStatus,
    PageFigureResult,
)
from core.models import PipelineStage, StageStatus
from services.figure_artifact_validator import FigureArtifactValidator
from services.figure_candidate_service import FigureCandidateService
from services.figure_extractor import FigureExtractionPlanner, FigureExtractor
from services.figure_group_service import FigureGroupService, stable_figure_filename
from services.figure_marker_validator import FigureMarkerValidator
from services.figure_matcher import FigureMatcher
from services.figure_reconciler import FigureReconciler
from services.figure_resolver import FigureResolver
from services.figure_marker_normalizer import canonical_marker
from services.page_layout_manifest_service import PageLayoutManifestService
from services.resolved_page_builder import (
    ManualMarkerPlacement,
    MarkerRepairRecord,
    ResolvedPageBuilder,
    ResolvedPageInput,
)
from core.layout_models import FigureGroup
from services.training_dataset_collector import TrainingDatasetCollector
from services.transcription_validator import FIGURE_MARKER_RE
from storage.database import Database
from storage.repository import ProjectRepository
from utils.hashing import file_sha256
from utils.logger import get_logger
from utils.paths import ensure_dir

logger = get_logger("figure_service")


class FigureService:
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
        cfg = (config or {}).get("figures") or (config or {})
        self.cfg = cfg
        norm_cfg = cfg.get("marker_normalization") or {}
        self.auto_resolve = bool(cfg.get("auto_resolve", True))
        self.caption_anchored_auto = bool(cfg.get("caption_anchored_auto", True))
        self.candidate_svc = FigureCandidateService(cfg)
        self.matcher = FigureMatcher(cfg)
        self.marker_validator = FigureMarkerValidator(
            allow_safe_syntax_repair=bool(
                norm_cfg.get("allow_safe_syntax_repair", True)
            )
        )
        self.planner = FigureExtractionPlanner()
        self.extractor = FigureExtractor(
            figures_dir=project_root / "figures",
            pdf_hash=self.pdf_hash,
            cfg=cfg,
        )
        self.artifact_validator = FigureArtifactValidator(cfg)
        self.resolver = FigureResolver(project_root / "resolved_pages")
        self.page_builder = ResolvedPageBuilder(project_root / "resolved_pages")
        self.layout_svc = PageLayoutManifestService(config)
        self.reconciler = FigureReconciler()
        self.group_svc = FigureGroupService()
        self.training = TrainingDatasetCollector(project_root)

    def process_page(
        self,
        page_number: int,
        *,
        force: bool = False,
        analyze_only: bool = False,
        cancel_check: Callable[[], bool] | None = None,
    ) -> PageFigureResult:
        canon_md_path = self.project_root / "markdown_pages" / f"page_{page_number:04d}.md"
        canon_json_path = self.project_root / "page_results" / f"page_{page_number:04d}.json"
        if not canon_md_path.exists() or not canon_json_path.exists():
            return PageFigureResult(
                page_number=page_number,
                stage_status=StageStatus.WAITING.value,
                error="no canonical transcription",
            )

        canonical_md = canon_md_path.read_text(encoding="utf-8")
        canon_sha = file_sha256(canon_md_path)
        payload = json.loads(canon_json_path.read_text(encoding="utf-8"))
        result = PageTranscriptionResult.model_validate(payload["result"])
        requests = self._build_requests(page_number, result)

        has_markers = bool(FIGURE_MARKER_RE.search(canonical_md))
        # Caption-first: even without markdown FIGURE markers, PDF "Fig./图 N"
        # is enough to drive automatic crop — defer empty-page exit until after layout.
        defer_empty = self.caption_anchored_auto
        if not requests and not has_markers and not defer_empty:
            resolved_path = self.resolver.copy_canonical(page_number, canonical_md)
            self._update_stage(page_number, StageStatus.SUCCESS, str(resolved_path))
            return PageFigureResult(
                page_number=page_number,
                stage_status=StageStatus.SUCCESS.value,
                resolved_path=str(resolved_path),
            )

        marker_report = self.marker_validator.validate(
            page_number=page_number,
            markdown=canonical_md,
            requests=requests,
        )
        # Caption-anchored mode: figure identity comes from PDF captions, not AI bbox
        marker_ok = (
            True
            if self.caption_anchored_auto
            else (marker_report.ok or marker_report.safe_marker_fix)
        )

        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            repo.upsert_stage_state(
                page_number,
                PipelineStage.FIGURES,
                StageStatus.RUNNING,
            )
        finally:
            db.close()

        doc = pymupdf.open(str(self.pdf_path))
        try:
            page = doc[page_number - 1]
            candidates = self.candidate_svc.discover(page, page_number)
            # Phase 9.5: layout manifest + bidirectional reconcile
            manifest = self.layout_svc.build_page(
                pdf_path=self.pdf_path,
                page_number=page_number,
                pdf_hash=self.pdf_hash,
                ai_requests=requests,
            )
            layout_path = self.layout_svc.write(self.project_root, manifest)
            requests = self._merge_caption_requests(
                page_number, requests, manifest.figure_groups, canonical_md
            )
            if not requests and not has_markers and not manifest.figure_groups:
                resolved_path = self.resolver.copy_canonical(page_number, canonical_md)
                self._update_stage(page_number, StageStatus.SUCCESS, str(resolved_path))
                return PageFigureResult(
                    page_number=page_number,
                    stage_status=StageStatus.SUCCESS.value,
                    resolved_path=str(resolved_path),
                )

            reconcile = self.reconciler.reconcile(
                manifest=manifest, ai_figures=result.figures
            )
            # Caption-anchored: reconcile mismatches are warnings, not blockers
            reconcile_blocking = bool(
                (self.cfg.get("reconcile") or {}).get("blocking", True)
            ) and not self.caption_anchored_auto
            any_review_pre = bool(reconcile.needs_review and reconcile_blocking)

            debug_dir = ensure_dir(
                self.project_root
                / "experiments"
                / "figures"
                / f"page_{page_number:04d}"
            )
            (debug_dir / "layout_reconcile.json").write_text(
                __import__("json").dumps(
                    {
                        "issues": reconcile.issues,
                        "warnings": reconcile.warnings,
                        "pdf_labels": reconcile.pdf_labels,
                        "ai_labels": reconcile.ai_labels,
                        "layout_path": str(layout_path),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            figure_rows: list[dict[str, Any]] = []
            figure_paths: dict[int, str] = {}
            figure_hashes: dict[int, str] = {}
            # Caption-anchored mode never opens the human review queue.
            any_review = False if self.caption_anchored_auto else (
                marker_report.needs_review or any_review_pre
            )
            any_failed = False
            page_w = float(page.rect.width)
            page_h = float(page.rect.height)

            for req in requests:
                if cancel_check and cancel_check():
                    break
                group = self.reconciler.prefer_group_for_request(
                    groups=manifest.figure_groups,
                    figure_index=req.figure_index,
                    caption=req.caption,
                )
                if group is not None:
                    req = FigureRequest(
                        page_number=req.page_number,
                        figure_index=req.figure_index,
                        marker=req.marker,
                        figure_type=req.figure_type,
                        caption=req.caption or group.caption,
                        ai_bbox_1000=req.ai_bbox_1000,
                        mermaid_candidate=req.mermaid_candidate,
                        figure_label=group.figure_label,
                        force_pdf_clip=bool(group.force_pdf_clip),
                        group_bbox_1000=group.bbox_1000 or req.ai_bbox_1000,
                    )
                if self.caption_anchored_auto:
                    req = self._ensure_auto_crop_request(
                        req, page_width=page_w, page_height=page_h
                    )

                match = self.matcher.match(req, candidates, marker_ok=marker_ok)
                # Caption / auto mode: always clip — never wait for human review.
                force_auto = bool(
                    self.caption_anchored_auto
                    or (self.auto_resolve and (req.group_bbox_1000 or req.ai_bbox_1000))
                )
                if force_auto and (req.group_bbox_1000 or req.ai_bbox_1000):
                    match = FigureMatch(
                        request=req,
                        candidate=match.candidate,
                        iou=match.iou,
                        containment=match.containment,
                        center_distance=match.center_distance,
                        score=max(match.score, 0.99),
                        strategy="caption_anchored"
                        if self.caption_anchored_auto
                        else (match.strategy or "auto_force"),
                        auto_resolvable=True,
                        reasons=list(match.reasons)
                        + (
                            ["caption_anchored_auto"]
                            if self.caption_anchored_auto
                            else ["force_auto_crop"]
                        ),
                    )
                plan = self.planner.plan(
                    match,
                    crop_dpi=int(self.cfg.get("crop_dpi", 300)),
                    padding_ratio=float(
                        self.cfg.get("crop_padding_page_ratio", 0.008)
                    ),
                )
                if force_auto and plan.method == FigureSourceMethod.UNRESOLVED:
                    plan = FigureExtractionPlan(
                        request=req,
                        method=FigureSourceMethod.PDF_CLIP,
                        clip_rect_pdf=None,
                        candidate=match.candidate,
                        match_score=match.score,
                        crop_dpi=int(self.cfg.get("crop_dpi", 300)),
                        padding_ratio=float(
                            self.cfg.get("crop_padding_page_ratio", 0.008)
                        ),
                        reasons=["force_auto_clip"],
                    )
                elif force_auto and getattr(req, "force_pdf_clip", False):
                    plan = FigureExtractionPlan(
                        request=req,
                        method=FigureSourceMethod.PDF_CLIP,
                        clip_rect_pdf=None,
                        candidate=match.candidate,
                        match_score=match.score,
                        crop_dpi=int(self.cfg.get("crop_dpi", 300)),
                        padding_ratio=float(
                            self.cfg.get("crop_padding_page_ratio", 0.008)
                        ),
                        reasons=list(plan.reasons) + ["force_pdf_clip"],
                    )

                status = (
                    FigureStatus.FAILED
                    if self.caption_anchored_auto
                    else FigureStatus.NEEDS_REVIEW
                )
                artifact_path: str | None = None
                art_hash = ""
                warnings = list(match.reasons)
                if group and group.warnings:
                    warnings.extend(group.warnings)
                if reconcile.issues and not self.caption_anchored_auto:
                    warnings.extend(reconcile.issues)
                elif reconcile.issues:
                    warnings.extend([f"reconcile_warn:{x}" for x in reconcile.issues])
                errors: list[str] = []

                if (
                    marker_report.needs_review
                    and not marker_report.safe_marker_fix
                    and not self.caption_anchored_auto
                ):
                    warnings.extend(marker_report.issues)
                elif reconcile.needs_review and reconcile_blocking:
                    any_review = True
                    status = FigureStatus.NEEDS_REVIEW
                elif (
                    (self.auto_resolve or self.caption_anchored_auto)
                    and match.auto_resolvable
                    and not analyze_only
                ):
                    art = self.extractor.extract(
                        doc,
                        page,
                        plan,
                        resolved_bbox_1000=req.group_bbox_1000 or req.ai_bbox_1000,
                        force=force,
                    )
                    art = self.artifact_validator.validate(
                        art,
                        page_width=page_w,
                        page_height=page_h,
                    )
                    # Caption mode: accept almost any written crop
                    if self.caption_anchored_auto and art.artifact_path:
                        if not art.valid:
                            art.valid = True
                            art.warnings = list(art.warnings) + [
                                "caption_auto_accepted"
                            ]
                            # Drop hard-fail codes that only existed to trigger review
                            art.errors = [
                                e
                                for e in (art.errors or [])
                                if e not in {"crop_too_small", "artifact_invalid"}
                            ]
                    if art.valid and art.artifact_path:
                        status = (
                            FigureStatus.CACHED
                            if art.cached
                            else FigureStatus.RESOLVED
                        )
                        artifact_path = art.artifact_path
                        if req.figure_label and artifact_path:
                            preferred = (
                                self.project_root
                                / "figures"
                                / stable_figure_filename(
                                    page_number,
                                    req.figure_label,
                                    Path(artifact_path).suffix,
                                )
                            )
                            try:
                                src = Path(artifact_path)
                                if src.exists() and preferred.resolve() != src.resolve():
                                    preferred.write_bytes(src.read_bytes())
                                    artifact_path = str(preferred)
                            except OSError:
                                pass
                        art_hash = art.artifact_hash
                        figure_paths[req.figure_index] = artifact_path
                        figure_hashes[req.figure_index] = art_hash
                        warnings.extend(art.warnings)
                    else:
                        any_failed = True
                        errors.extend(art.errors or ["extract_failed"])
                        if self.caption_anchored_auto:
                            status = FigureStatus.FAILED
                            warnings.append("auto_crop_failed_no_review")
                        else:
                            status = FigureStatus.NEEDS_REVIEW
                            any_review = True
                elif analyze_only:
                    # Analysis pass: never enqueue human review in caption mode
                    status = (
                        FigureStatus.RESOLVED
                        if self.caption_anchored_auto and match.auto_resolvable
                        else FigureStatus.NEEDS_REVIEW
                    )
                    if status == FigureStatus.NEEDS_REVIEW:
                        any_review = True
                    warnings.append("analyze_only")
                else:
                    if self.caption_anchored_auto:
                        # Still try a last-resort page band crop
                        fallback_bbox = req.group_bbox_1000 or req.ai_bbox_1000
                        if fallback_bbox and not analyze_only:
                            plan = FigureExtractionPlan(
                                request=req,
                                method=FigureSourceMethod.PDF_CLIP,
                                clip_rect_pdf=None,
                                candidate=None,
                                match_score=0.5,
                                crop_dpi=int(self.cfg.get("crop_dpi", 300)),
                                padding_ratio=float(
                                    self.cfg.get("crop_padding_page_ratio", 0.008)
                                ),
                                reasons=["last_resort_clip"],
                            )
                            art = self.extractor.extract(
                                doc,
                                page,
                                plan,
                                resolved_bbox_1000=fallback_bbox,
                                force=True,
                            )
                            if art.artifact_path:
                                status = FigureStatus.RESOLVED
                                artifact_path = art.artifact_path
                                art_hash = art.artifact_hash
                                figure_paths[req.figure_index] = artifact_path
                                figure_hashes[req.figure_index] = art_hash
                                warnings.append("last_resort_auto_crop")
                            else:
                                any_failed = True
                                status = FigureStatus.FAILED
                                errors.append("auto_crop_unavailable")
                        else:
                            any_failed = True
                            status = FigureStatus.FAILED
                            errors.append("no_bbox_for_auto_crop")
                    else:
                        any_review = True
                        status = FigureStatus.NEEDS_REVIEW

                repair = next(
                    (m for m in marker_report.safe_repairs if m.index == req.figure_index),
                    None,
                )
                loose = next(
                    (m for m in marker_report.loose_markers if m.index == req.figure_index),
                    None,
                )
                row = {
                    "page_number": page_number,
                    "figure_index": req.figure_index,
                    "figure_label": req.figure_label,
                    "status": status.value,
                    "marker": req.marker,
                    "figure_type": req.figure_type,
                    "caption": req.caption,
                    "ai_bbox_1000": req.ai_bbox_1000,
                    "matched_bbox_1000": (
                        match.candidate.bbox_1000 if match.candidate else None
                    ),
                    "resolved_bbox_1000": (
                        req.group_bbox_1000
                        or (
                            match.candidate.bbox_1000
                            if match.candidate
                            else req.ai_bbox_1000
                        )
                    ),
                    "source_method": plan.method.value,
                    "artifact_path": artifact_path,
                    "artifact_hash": art_hash,
                    "match_score": match.score,
                    "auto_resolved": status
                    in {FigureStatus.RESOLVED, FigureStatus.CACHED},
                    "marker_original": (loose.original if loose else req.marker),
                    "marker_normalized": (
                        repair.normalized
                        if repair
                        else canonical_marker(page_number, req.figure_index)
                    ),
                    "marker_repair_type": (
                        marker_report.marker_repair_type if repair else None
                    ),
                    "marker_repaired": bool(repair),
                    "warnings": warnings,
                    "errors": errors,
                    "force_pdf_clip": req.force_pdf_clip,
                }
                figure_rows.append(row)
                self._persist_figure(row)
                self._write_debug(
                    debug_dir / f"fig{req.figure_index:02d}",
                    req,
                    candidates,
                    match,
                    plan,
                    row,
                )

            # Training snapshot (no model training)
            try:
                self.training.record_page(
                    page_number=page_number,
                    page_png=self.project_root / "pages" / f"page_{page_number:04d}.png",
                    layout_json=layout_path,
                    figures=figure_rows,
                    corrections={"reconcile_issues": reconcile.issues},
                )
            except OSError:
                logger.exception("training dataset write failed")

            resolved_path: str | None = None
            if not analyze_only and not any_review and not any_failed:
                repairs = [
                    MarkerRepairRecord(
                        figure_index=m.index,
                        original=m.original,
                        normalized=m.normalized,
                        repair_type=marker_report.marker_repair_type or "syntax_only",
                    )
                    for m in marker_report.safe_repairs
                ]
                placements = self._auto_place_caption_markers(
                    page_number, canonical_md, requests, figure_paths
                )
                md, _ = self.page_builder.build(
                    ResolvedPageInput(
                        page_number=page_number,
                        canonical_md=canonical_md,
                        marker_repairs=repairs,
                        manual_placements=placements,
                        figure_paths=figure_paths,
                        figure_hashes=figure_hashes,
                    )
                )
                out = self.page_builder.write_resolved(page_number, md, force=force)
                resolved_path = str(out)
                stage = StageStatus.SUCCESS
            elif (
                not analyze_only
                and self.caption_anchored_auto
                and figure_paths
                and not any_review
            ):
                # Partial success is OK — do not open review queue
                repairs = [
                    MarkerRepairRecord(
                        figure_index=m.index,
                        original=m.original,
                        normalized=m.normalized,
                        repair_type=marker_report.marker_repair_type or "syntax_only",
                    )
                    for m in marker_report.safe_repairs
                ]
                placements = self._auto_place_caption_markers(
                    page_number, canonical_md, requests, figure_paths
                )
                md, _ = self.page_builder.build(
                    ResolvedPageInput(
                        page_number=page_number,
                        canonical_md=canonical_md,
                        marker_repairs=repairs,
                        manual_placements=placements,
                        figure_paths=figure_paths,
                        figure_hashes=figure_hashes,
                    )
                )
                out = self.page_builder.write_resolved(page_number, md, force=force)
                resolved_path = str(out)
                stage = StageStatus.SUCCESS
                any_failed = False
            elif analyze_only:
                stage = StageStatus.NEEDS_REVIEW if any_review else StageStatus.SUCCESS
            else:
                if self.caption_anchored_auto:
                    stage = StageStatus.FAILED if any_failed else StageStatus.SUCCESS
                else:
                    stage = (
                        StageStatus.NEEDS_REVIEW if any_review else StageStatus.FAILED
                    )
            self._update_stage(
                page_number,
                stage,
                resolved_path,
                error=";".join(
                    list(marker_report.issues) + list(reconcile.issues)
                )
                or None,
            )
            _ = canon_sha  # provenance anchor — canonical untouched
            return PageFigureResult(
                page_number=page_number,
                stage_status=stage.value,
                figures=figure_rows,
                resolved_path=resolved_path,
            )
        finally:
            doc.close()

    def _build_requests(
        self, page_number: int, result: PageTranscriptionResult
    ) -> list[FigureRequest]:
        out: list[FigureRequest] = []
        for fig in result.figures:
            out.append(
                FigureRequest(
                    page_number=page_number,
                    figure_index=fig.figure_index,
                    marker=fig.marker,
                    figure_type=fig.figure_type,
                    caption=fig.caption,
                    ai_bbox_1000=fig.bbox_1000,
                    mermaid_candidate=fig.mermaid_candidate,
                )
            )
        return out

    def _merge_caption_requests(
        self,
        page_number: int,
        requests: list[FigureRequest],
        groups: list[FigureGroup],
        canonical_md: str,
    ) -> list[FigureRequest]:
        """Drive figure crop from PDF Fig./图 N captions; AI bbox is optional."""
        if not groups:
            return requests

        used_labels: set[str] = set()
        used_indices = {r.figure_index for r in requests}
        out: list[FigureRequest] = []
        claimed: set[str] = set()

        for req in requests:
            group = self.reconciler.prefer_group_for_request(
                groups=groups,
                figure_index=req.figure_index,
                caption=req.caption,
            )
            if group is None:
                out.append(req)
                continue
            claimed.add(group.ensure_id())
            used_labels.add(group.figure_label.lower())
            out.append(
                FigureRequest(
                    page_number=req.page_number,
                    figure_index=req.figure_index,
                    marker=req.marker or canonical_marker(page_number, req.figure_index),
                    figure_type=req.figure_type or "image",
                    caption=req.caption or group.caption,
                    ai_bbox_1000=req.ai_bbox_1000,
                    mermaid_candidate=req.mermaid_candidate,
                    figure_label=group.figure_label,
                    force_pdf_clip=bool(group.force_pdf_clip),
                    group_bbox_1000=group.bbox_1000 or req.ai_bbox_1000,
                )
            )

        next_idx = (max(used_indices) + 1) if used_indices else 1
        for group in groups:
            if group.ensure_id() in claimed:
                continue
            label_key = group.figure_label.lower()
            if label_key in used_labels:
                continue
            # Prefer numeric label as index when free
            idx: int | None = None
            digits = "".join(ch for ch in group.figure_label if ch.isdigit())
            if digits and group.figure_label.lower() == digits:
                candidate = int(digits)
                if candidate not in used_indices:
                    idx = candidate
            if idx is None:
                idx = next_idx
                next_idx += 1
            used_indices.add(idx)
            used_labels.add(label_key)
            out.append(
                FigureRequest(
                    page_number=page_number,
                    figure_index=idx,
                    marker=canonical_marker(page_number, idx),
                    figure_type="image",
                    caption=group.caption,
                    ai_bbox_1000=group.bbox_1000,
                    mermaid_candidate=False,
                    figure_label=group.figure_label,
                    force_pdf_clip=True,
                    group_bbox_1000=group.bbox_1000,
                )
            )

        out.sort(key=lambda r: r.figure_index)
        _ = canonical_md  # reserved for future caption-text alignment
        return out

    @staticmethod
    def _ensure_auto_crop_request(
        req: FigureRequest,
        *,
        page_width: float,
        page_height: float,
    ) -> FigureRequest:
        """Guarantee label + bbox so caption mode can always PDF-clip without review."""
        label = req.figure_label
        if not label and req.caption:
            m = re.search(
                r"(?i)(?:fig(?:ure)?|abb)\.?\s*([0-9]+[a-z]?)|图\s*([0-9]+[a-z]?)",
                req.caption,
            )
            if m:
                label = m.group(1) or m.group(2)
        bbox = req.group_bbox_1000 or req.ai_bbox_1000
        if bbox is None:
            # Mid-page band fallback (above typical caption zone)
            bbox = (40, 120, 960, 620)
            if page_width > 0 and page_height > 0:
                # keep generic 1000-space band
                pass
        return FigureRequest(
            page_number=req.page_number,
            figure_index=req.figure_index,
            marker=req.marker or canonical_marker(req.page_number, req.figure_index),
            figure_type=req.figure_type or "image",
            caption=req.caption,
            ai_bbox_1000=req.ai_bbox_1000 or bbox,
            mermaid_candidate=req.mermaid_candidate,
            figure_label=label or str(req.figure_index),
            force_pdf_clip=True,
            group_bbox_1000=bbox,
        )

    def _auto_place_caption_markers(
        self,
        page_number: int,
        canonical_md: str,
        requests: list[FigureRequest],
        figure_paths: dict[int, str],
    ) -> list[ManualMarkerPlacement]:
        """If markdown has no FIGURE marker for a resolved caption crop, insert one."""
        if not self.caption_anchored_auto:
            return []
        present = {
            int(m[1])
            for m in FIGURE_MARKER_RE.findall(canonical_md)
            if int(m[0]) == page_number
        }
        placements: list[ManualMarkerPlacement] = []
        used_offsets: set[int] = set()
        for req in requests:
            if req.figure_index not in figure_paths:
                continue
            if req.figure_index in present:
                continue
            offset = self._find_caption_insert_offset(
                canonical_md, req.caption, req.figure_label, used_offsets
            )
            if offset is None:
                offset = len(canonical_md.rstrip()) + 2
            used_offsets.add(offset)
            placements.append(
                ManualMarkerPlacement(
                    figure_index=req.figure_index,
                    page_number=page_number,
                    char_offset=offset,
                    before_context=(canonical_md[max(0, offset - 40) : offset]),
                    after_context=canonical_md[offset : offset + 40],
                )
            )
        return placements

    @staticmethod
    def _find_caption_insert_offset(
        markdown: str,
        caption: str | None,
        figure_label: str | None,
        used_offsets: set[int],
    ) -> int | None:
        """Place marker just above the caption line (figure sits above Fig./图 N)."""
        needles: list[str] = []
        if caption:
            needles.append(caption.strip()[:80])
        if figure_label:
            needles.extend(
                [
                    f"Fig. {figure_label}",
                    f"Figure {figure_label}",
                    f"Fig {figure_label}",
                    f"图 {figure_label}",
                    f"图{figure_label}",
                ]
            )
        for needle in needles:
            if not needle:
                continue
            pos = markdown.find(needle)
            if pos < 0:
                continue
            # Insert at start of the caption line
            line_start = markdown.rfind("\n", 0, pos) + 1
            if line_start in used_offsets:
                continue
            return line_start
        if figure_label:
            m = re.search(
                rf"(?im)^(?:.*?\b(?:fig(?:ure)?|abb)\.?\s*{re.escape(figure_label)}\b"
                rf"|.*图\s*{re.escape(figure_label)}\b)",
                markdown,
            )
            if m and m.start() not in used_offsets:
                return m.start()
        return None

    def _persist_figure(self, row: dict[str, Any]) -> None:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            repo.upsert_figure(
                page_number=int(row["page_number"]),
                figure_index=int(row["figure_index"]),
                status=str(row["status"]),
                file_path=row.get("artifact_path"),
                marker=row.get("marker"),
                figure_type=row.get("figure_type"),
                caption=row.get("caption"),
                ai_bbox_1000=row.get("ai_bbox_1000"),
                matched_bbox_1000=row.get("matched_bbox_1000"),
                resolved_bbox_1000=row.get("resolved_bbox_1000"),
                source_method=row.get("source_method"),
                artifact_hash=row.get("artifact_hash"),
                match_score=row.get("match_score"),
                auto_resolved=bool(row.get("auto_resolved")),
                marker_original=row.get("marker_original"),
                marker_normalized=row.get("marker_normalized"),
                marker_repair_type=row.get("marker_repair_type"),
                marker_repaired=bool(row.get("marker_repaired")),
                warnings=row.get("warnings"),
                error_message=";".join(row.get("errors") or []),
            )
            conn = db.connect()
            conn.execute(
                """
                UPDATE figures SET
                    figure_label = ?,
                    force_pdf_clip = ?,
                    updated_at = ?
                WHERE page_number = ? AND figure_index = ?
                """,
                (
                    row.get("figure_label"),
                    1 if row.get("force_pdf_clip") else 0,
                    datetime.now(timezone.utc).isoformat(),
                    int(row["page_number"]),
                    int(row["figure_index"]),
                ),
            )
            conn.commit()
        finally:
            db.close()

    def _update_stage(
        self,
        page_number: int,
        status: StageStatus,
        artifact_path: str | None = None,
        error: str | None = None,
    ) -> None:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            repo.upsert_stage_state(
                page_number,
                PipelineStage.FIGURES,
                status,
                artifact_path=artifact_path,
                error_message=error,
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
        finally:
            db.close()

    @staticmethod
    def _write_debug(
        base: Path,
        req: FigureRequest,
        candidates: list,
        match,
        plan,
        row: dict,
    ) -> None:
        ensure_dir(base)
        report = {
            "request": {
                "page": req.page_number,
                "index": req.figure_index,
                "ai_bbox": req.ai_bbox_1000,
            },
            "candidates": [
                {
                    "id": c.candidate_id,
                    "type": c.candidate_type,
                    "bbox_1000": c.bbox_1000,
                    "xref": c.xref,
                    "digest": c.digest,
                }
                for c in candidates
            ],
            "match": {
                "score": match.score,
                "strategy": match.strategy,
                "auto": match.auto_resolvable,
                "reasons": match.reasons,
            },
            "plan": {"method": plan.method.value},
            "result": row,
        }
        (base / "candidate_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
