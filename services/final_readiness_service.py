"""Final readiness gate before freezing final.md."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.models import PipelineStage, StageStatus
from services.assemble_readiness_service import AssembleReadinessService
from services.clean_readiness_service import CleanReadinessService
from services.figure_readiness_service import FigureReadinessService
from storage.database import Database
from storage.repository import ProjectRepository

_OK = {StageStatus.SUCCESS.value, StageStatus.CACHED.value}
_RUNNING = {StageStatus.RUNNING.value}


class FinalReadinessService:
    def __init__(
        self,
        *,
        project_root: Path,
        db_path: Path,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.project_root = project_root
        self.db_path = db_path
        self.config = config or {}
        self.figure = FigureReadinessService(
            project_root=project_root, db_path=db_path, config=self.config
        )
        self.assemble = AssembleReadinessService(
            project_root=project_root, db_path=db_path, config=self.config
        )
        self.clean = CleanReadinessService(
            project_root=project_root, db_path=db_path, config=self.config
        )

    def summarize(self, page_numbers: list[int] | None = None) -> dict[str, Any]:
        pages = page_numbers or self._all_pages()
        blocking: list[str] = []
        warnings: list[str] = []

        if not self.project_root.exists():
            blocking.append("project_missing")
        source_pdf = self._source_pdf()
        if source_pdf is None or not source_pdf.exists():
            blocking.append("source_pdf_missing")

        clean_md = self.project_root / "intermediate" / "clean.md"
        if not clean_md.exists() or not clean_md.read_text(encoding="utf-8").strip():
            blocking.append("clean_md_missing")

        fig = self.figure.summarize()
        asm = self.assemble.summarize(pages)
        cln = self.clean.summarize(pages)

        if not fig.get("ready"):
            blocking.append(
                f"figures_not_ready:remaining={fig.get('remaining_reviews', 0)}"
            )
        if not asm.get("ready"):
            blocking.append("assemble_not_ready")
        if not cln.get("ready"):
            blocking.append("clean_not_ready")

        running = self._running_pages(pages)
        if running:
            blocking.append(f"stages_running:{running}")

        review_blocking = self._blocking_reviews()
        if review_blocking:
            blocking.extend(review_blocking)

        # Release warning only — does not block this document
        warnings.append("math_heavy_validation_pending")

        if blocking:
            status = "NOT_READY"
        elif warnings:
            status = "READY_WITH_WARNINGS"
        else:
            status = "READY"

        return {
            "status": status,
            "ready": status in {"READY", "READY_WITH_WARNINGS"},
            "pages": len(pages),
            "blocking": blocking,
            "warnings": warnings,
            "transcription_ready": asm.get("transcription_ready"),
            "figures_ready": fig.get("ready"),
            "assemble_ready": asm.get("ready"),
            "clean_ready": cln.get("ready"),
            "clean_md_exists": clean_md.exists(),
            "source_pdf": str(source_pdf) if source_pdf else None,
        }

    def _source_pdf(self) -> Path | None:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            proj = repo.get_project()
            if not proj:
                return None
            path = Path(proj.get("source_path") or "")
            return path if path.exists() else None
        finally:
            db.close()

    def _all_pages(self) -> list[int]:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            return [int(p["page_number"]) for p in repo.list_pages()]
        finally:
            db.close()

    def _running_pages(self, pages: list[int]) -> list[str]:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        out: list[str] = []
        try:
            for stage in (
                PipelineStage.RENDER,
                PipelineStage.TRANSCRIBE,
                PipelineStage.FIGURES,
                PipelineStage.CLEAN,
            ):
                for p in pages:
                    st = repo.get_stage_state(p, stage)
                    if (st or {}).get("status") in _RUNNING:
                        out.append(f"{stage.value}:{p}")
        finally:
            db.close()
        return out

    def _blocking_reviews(self) -> list[str]:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        issues: list[str] = []
        try:
            figs = repo.list_figure_review_items()
            if figs:
                issues.append(f"figure_reviews:{len(figs)}")
            cleans = repo.list_cleaner_review_items()
            if cleans:
                issues.append(f"cleaner_reviews:{len(cleans)}")
            # transcription review pages
            reviews = repo.list_review_pages() if hasattr(repo, "list_review_pages") else []
            if reviews:
                issues.append(f"transcription_reviews:{len(reviews)}")
        finally:
            db.close()
        return issues
