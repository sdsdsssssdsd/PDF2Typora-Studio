"""Figure readiness gate before Markdown Assemble (Phase 6.5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.models import PipelineStage, StageStatus
from storage.database import Database
from storage.repository import ProjectRepository


class FigureReadinessService:
    BLOCKING_STATUSES = {
        StageStatus.WAITING.value,
        StageStatus.RUNNING.value,
        StageStatus.FAILED.value,
        StageStatus.NEEDS_REVIEW.value,
    }

    def __init__(self, *, project_root: Path, db_path: Path, config: dict[str, Any]) -> None:
        self.project_root = project_root
        self.db_path = db_path
        fig_cfg = config.get("figures") or {}
        self.caption_anchored_auto = bool(fig_cfg.get("caption_anchored_auto", True))
        self.require_all = bool(
            (fig_cfg.get("readiness") or {}).get("require_all_resolved", True)
        )
        # Caption-anchored pipeline should not trap users in review queues.
        default_override = self.caption_anchored_auto
        self.allow_unresolved = bool(
            (fig_cfg.get("readiness") or {}).get(
                "allow_unresolved_override", default_override
            )
        )
        if self.caption_anchored_auto:
            self.BLOCKING_STATUSES = {
                StageStatus.WAITING.value,
                StageStatus.RUNNING.value,
                StageStatus.FAILED.value,
            }

    def summarize(self) -> dict[str, Any]:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            figures = repo.list_figures()
            stages = repo.list_stage_states(PipelineStage.FIGURES)
        finally:
            db.close()

        by_status: dict[str, int] = {}
        for f in figures:
            st = f.get("status") or "unknown"
            by_status[st] = by_status.get(st, 0) + 1

        page_blocking = [
            s
            for s in stages
            if s.get("status") in self.BLOCKING_STATUSES
        ]
        review_count = by_status.get("needs_review", 0) + len(page_blocking)

        ready = self.is_ready_for_assemble(stages=stages, figures=figures)
        return {
            "ready": ready,
            "figures_total": len(figures),
            "resolved": by_status.get("resolved", 0) + by_status.get("cached", 0),
            "skipped": by_status.get("skipped", 0),
            "needs_review": by_status.get("needs_review", 0),
            "failed": by_status.get("failed", 0),
            "remaining_reviews": review_count,
            "pages_blocking": len(page_blocking),
            "by_status": by_status,
        }

    def is_ready_for_assemble(
        self,
        *,
        stages: list[dict[str, Any]] | None = None,
        figures: list[dict[str, Any]] | None = None,
    ) -> bool:
        if self.allow_unresolved and not self.require_all:
            return True
        if self.caption_anchored_auto and self.allow_unresolved:
            # Only hard-fail pages block assemble; stale needs_review is ignored.
            pass

        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            if stages is None:
                stages = repo.list_stage_states(PipelineStage.FIGURES)
            if figures is None:
                figures = repo.list_figures()
        finally:
            db.close()

        transcribed = self._transcribed_pages()
        if not transcribed:
            return True

        fig_block = {"failed", "waiting"}
        if not self.caption_anchored_auto:
            fig_block.add("needs_review")

        stage_by_page = {int(s["page_number"]): s for s in stages}
        for page in transcribed:
            js = self.project_root / "page_results" / f"page_{page:04d}.json"
            if not js.exists():
                continue
            import json

            payload = json.loads(js.read_text(encoding="utf-8"))
            figs = payload.get("result", {}).get("figures") or []
            if not figs:
                st = stage_by_page.get(page, {}).get("status")
                if st and st in self.BLOCKING_STATUSES:
                    return False
                continue

            page_figs = [f for f in figures if int(f["page_number"]) == page]
            if not page_figs and figs:
                # Caption-only pages may have no AI figures list — OK if stage success
                st = stage_by_page.get(page, {}).get("status")
                if st in self.BLOCKING_STATUSES:
                    return False
                continue
            for f in page_figs:
                if f.get("status") in fig_block:
                    return False
            st = stage_by_page.get(page, {}).get("status")
            if st in self.BLOCKING_STATUSES:
                return False

        if figures:
            for f in figures:
                if f.get("status") in fig_block:
                    return False
        return True

    def _transcribed_pages(self) -> list[int]:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            rows = repo.list_stage_states(PipelineStage.TRANSCRIBE)
        finally:
            db.close()
        ok = {
            StageStatus.SUCCESS.value,
            StageStatus.CACHED.value,
        }
        return sorted(
            int(r["page_number"])
            for r in rows
            if r.get("status") in ok
        )
