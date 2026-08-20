"""Clean document readiness gate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.models import PipelineStage, StageStatus
from storage.database import Database
from storage.repository import ProjectRepository


class CleanReadinessService:
    OK = {StageStatus.SUCCESS.value, StageStatus.CACHED.value}

    def __init__(self, *, project_root: Path, db_path: Path, config: dict[str, Any] | None = None) -> None:
        self.project_root = project_root
        self.db_path = db_path
        self.config = config or {}

    def summarize(self, page_numbers: list[int] | None = None) -> dict[str, Any]:
        pages = page_numbers or self._all_pages()
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            stages = {
                int(s["page_number"]): s
                for s in repo.list_stage_states(PipelineStage.CLEAN)
            }
        finally:
            db.close()

        success = 0
        review = 0
        failed = 0
        waiting = 0
        for p in pages:
            st = (stages.get(p) or {}).get("status")
            if st in self.OK:
                success += 1
            elif st == StageStatus.NEEDS_REVIEW.value:
                review += 1
            elif st == StageStatus.FAILED.value:
                failed += 1
            else:
                waiting += 1

        clean_exists = (self.project_root / "intermediate" / "clean.md").exists()
        ready = success == len(pages) and len(pages) > 0 and review == 0 and failed == 0
        return {
            "ready": ready,
            "pages": len(pages),
            "success": success,
            "needs_review": review,
            "failed": failed,
            "waiting": waiting,
            "clean_md_exists": clean_exists,
            "label": "READY FOR CLEAN DOCUMENT" if ready else "NOT READY",
        }

    def _all_pages(self) -> list[int]:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            return [int(p["page_number"]) for p in repo.list_pages()]
        finally:
            db.close()
