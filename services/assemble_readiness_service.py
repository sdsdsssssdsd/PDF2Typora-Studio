"""Assemble readiness gate (Phase 7)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.models import PipelineStage, StageStatus
from services.figure_readiness_service import FigureReadinessService
from services.page_source_resolver import PageSourceResolver
from storage.database import Database
from storage.repository import ProjectRepository


class AssembleReadinessService:
    def __init__(
        self,
        *,
        project_root: Path,
        db_path: Path,
        config: dict[str, Any],
    ) -> None:
        self.project_root = project_root
        self.db_path = db_path
        self.config = config
        assemble_cfg = config.get("assemble") or {}
        self.allow_unresolved = bool(
            assemble_cfg.get("allow_unresolved_figures", False)
        )
        self.require_continuity = bool(
            assemble_cfg.get("require_continuity_review", False)
        )
        self.figure_ready = FigureReadinessService(
            project_root=project_root, db_path=db_path, config=config
        )
        self.resolver = PageSourceResolver(
            project_root=project_root, db_path=db_path
        )

    def summarize(self, page_numbers: list[int] | None = None) -> dict[str, Any]:
        pages = page_numbers or self._all_pages()
        fig = self.figure_ready.summarize()
        transcription_ready, missing_canonical, running_pages = self._transcription_check(
            pages
        )
        entries, source_errors = self.resolver.resolve_pages(
            pages, allow_unresolved_figures=self.allow_unresolved
        )
        figures_ready = bool(fig.get("ready")) or self.allow_unresolved
        sources_complete = not source_errors and len(entries) == len(pages)

        blocking: list[str] = []
        if not transcription_ready:
            blocking.append(f"Missing Canonical Pages: {len(missing_canonical)}")
            if missing_canonical:
                blocking.append(
                    "Missing: " + ",".join(f"{p:04d}" for p in missing_canonical[:8])
                )
        if not figures_ready:
            blocking.append(
                f"Figure Reviews Remaining: {fig.get('remaining_reviews', 0)}"
            )
        if source_errors:
            for e in source_errors[:8]:
                blocking.append(e)
        if running_pages:
            blocking.append(f"Pages Running: {len(running_pages)}")

        ready = (
            transcription_ready
            and figures_ready
            and sources_complete
            and not running_pages
        )
        return {
            "ready": ready,
            "transcription_ready": transcription_ready,
            "figures_ready": figures_ready,
            "sources_complete": sources_complete,
            "pages": len(pages),
            "resolved_sources": sum(
                1 for e in entries if e.source_type == "resolved"
            ),
            "canonical_sources": sum(
                1 for e in entries if e.source_type == "canonical"
            ),
            "missing_canonical": missing_canonical,
            "source_errors": source_errors,
            "running_pages": running_pages,
            "figure_summary": fig,
            "blocking": blocking,
            "allow_unresolved_figures": self.allow_unresolved,
            "require_continuity_review": self.require_continuity,
        }

    def is_ready(self, page_numbers: list[int] | None = None) -> bool:
        return bool(self.summarize(page_numbers).get("ready"))

    def _all_pages(self) -> list[int]:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            return [int(p["page_number"]) for p in repo.list_pages()]
        finally:
            db.close()

    def _transcription_check(
        self, pages: list[int]
    ) -> tuple[bool, list[int], list[int]]:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            missing: list[int] = []
            running: list[int] = []
            ok = {StageStatus.SUCCESS.value, StageStatus.CACHED.value}
            for page in pages:
                for stage in (
                    PipelineStage.RENDER,
                    PipelineStage.TRANSCRIBE,
                ):
                    st = repo.get_stage_state(page, stage)
                    status = (st or {}).get("status")
                    if status == StageStatus.RUNNING.value:
                        running.append(page)
                md = self.project_root / "markdown_pages" / f"page_{page:04d}.md"
                js = self.project_root / "page_results" / f"page_{page:04d}.json"
                # Disk artifacts are authoritative: heal stale/missing DB stage.
                if md.exists() and js.exists():
                    tr = repo.get_stage_state(page, PipelineStage.TRANSCRIBE)
                    if (tr or {}).get("status") not in ok:
                        repo.upsert_stage_state(
                            page,
                            PipelineStage.TRANSCRIBE,
                            StageStatus.SUCCESS,
                            artifact_path=str(js),
                        )
                    continue
                tr = repo.get_stage_state(page, PipelineStage.TRANSCRIBE)
                if (tr or {}).get("status") not in ok:
                    missing.append(page)
                    continue
                if not md.exists():
                    missing.append(page)
            return (not missing and not running), missing, sorted(set(running))
        finally:
            db.close()
