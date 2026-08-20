"""Phase 6.5 live pilot — resolve figures 3/4/7 via review service."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.config_manager import load_config
from core.models import PipelineStage, StageStatus
from services.batch_figure_service import BatchFigureService
from services.figure_readiness_service import FigureReadinessService
from services.figure_review_service import FigureReviewService
from storage.database import Database
from storage.repository import ProjectRepository
from utils.hashing import file_sha256

PROJECT = ROOT / "workspace" / "_phase4_vision" / "O-001_Kuzilek2017_DataPaper"
REPORT = ROOT / "phase65_live_report.json"
PILOT_PAGES = (3, 4, 7)


def _canonical_hashes() -> dict[str, str]:
    out = {}
    for p in PILOT_PAGES:
        path = PROJECT / "markdown_pages" / f"page_{p:04d}.md"
        if path.exists():
            out[f"page_{p:04d}"] = file_sha256(path)
    return out


def _resolve_via_review(svc: FigureReviewService, page: int, fig_idx: int) -> dict:
    ctx = svc.load_context(page, fig_idx)
    mr = ctx["marker_report"]
    candidates = ctx.get("candidates") or []
    req = ctx.get("request")

    bbox = None
    cand_id = None
    if req and req.ai_bbox_1000:
        bbox = req.ai_bbox_1000
    if not bbox:
        raster = [c for c in candidates if c.candidate_type == "raster" and c.bbox_1000]
        if not raster:
            raster = [c for c in candidates if c.bbox_1000]
        if raster:
            best = raster[0]
            if req:
                m = svc.matcher.match(req, candidates, marker_ok=True)
                if m.candidate and m.candidate.bbox_1000:
                    bbox = m.candidate.bbox_1000
                    cand_id = m.candidate.candidate_id
                else:
                    bbox = best.bbox_1000
                    cand_id = best.candidate_id
            else:
                bbox = best.bbox_1000
                cand_id = best.candidate_id

    result: dict = {"page": page, "figure_index": fig_idx, "issues": mr.issues}
    if not bbox:
        result["status"] = "needs_review"
        result["reason"] = "no_bbox"
        return result

    if "missing_marker" in mr.issues:
        canon = ctx["canonical_md"]
        offset = len(canon) // 2
        before = canon[max(0, offset - 30) : offset]
        after = canon[offset : offset + 30]
        svc.confirm_marker_placement(
            page_number=page,
            figure_index=fig_idx,
            char_offset=offset,
            before_context=before,
            after_context=after,
        )

    if "marker_index_conflict" in mr.issues:
        loose = mr.loose_markers
        if loose:
            svc.reassociate_marker(
                page_number=page,
                figure_index=fig_idx,
                marker_md_index=loose[0].index,
            )

    row = svc.accept_figure(
        page_number=page,
        figure_index=fig_idx,
        bbox_1000=tuple(bbox),
        candidate_id=cand_id,
        review_action="pilot_accept",
    )
    result["status"] = row.get("status")
    result["artifact"] = row.get("artifact_path")
    return result


def main() -> int:
    pdf = PROJECT / "source.pdf"
    db_path = PROJECT / "project.db"
    canon_before = _canonical_hashes()

    batch = BatchFigureService(
        project_root=PROJECT,
        pdf_path=pdf,
        db_path=db_path,
        pdf_hash=file_sha256(pdf),
        config=load_config(),
    )
    batch_summary = batch.process_pages(list(PILOT_PAGES), force=True)

    review = FigureReviewService(
        project_root=PROJECT,
        pdf_path=pdf,
        db_path=db_path,
        pdf_hash=file_sha256(pdf),
        config=load_config(),
    )

    db = Database(db_path)
    db.initialize()
    repo = ProjectRepository(db)
    pending = repo.list_figure_review_items()
    db.close()

    resolutions = []
    for item in pending:
        p = int(item["page_number"])
        idx = int(item["figure_index"])
        if p in PILOT_PAGES:
            resolutions.append(_resolve_via_review(review, p, idx))

    readiness = FigureReadinessService(
        project_root=PROJECT, db_path=db_path, config=load_config()
    ).summarize()

    canon_after = _canonical_hashes()
    canonical_unchanged = canon_before == canon_after

    report = {
        "phase": "6.5",
        "pilot_pages": list(PILOT_PAGES),
        "batch_summary": batch_summary,
        "resolutions": resolutions,
        "readiness": readiness,
        "canonical_sha256_unchanged": canonical_unchanged,
        "canonical_before": canon_before,
        "canonical_after": canon_after,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
