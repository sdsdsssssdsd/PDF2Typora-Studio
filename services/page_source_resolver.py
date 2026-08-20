"""Resolve which markdown artifact to use for each physical page."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.assemble_models import PageSourceEntry, PageSourceType
from core.models import PipelineStage, StageStatus
from storage.database import Database
from storage.repository import ProjectRepository
from utils.hashing import file_sha256
from utils.logger import get_logger

logger = get_logger("page_source_resolver")

_OK_TRANSCRIBE = {StageStatus.SUCCESS.value, StageStatus.CACHED.value}
_OK_FIGURES = {StageStatus.SUCCESS.value, StageStatus.CACHED.value}


class PageSourceResolver:
    def __init__(self, *, project_root: Path, db_path: Path) -> None:
        self.project_root = project_root
        self.db_path = db_path

    def resolve_pages(
        self,
        page_numbers: list[int] | tuple[int, ...],
        *,
        allow_unresolved_figures: bool = False,
    ) -> tuple[list[PageSourceEntry], list[str]]:
        """Return ordered sources and blocking errors."""
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            pages = repo.list_pages()
            page_set = {int(p["page_number"]) for p in pages}
            entries: list[PageSourceEntry] = []
            errors: list[str] = []

            for page in sorted(int(p) for p in page_numbers):
                if page not in page_set:
                    errors.append(f"page_{page:04d}_not_in_db")
                    continue
                entry, err = self._resolve_one(
                    repo, page, allow_unresolved_figures=allow_unresolved_figures
                )
                if err:
                    errors.append(err)
                if entry:
                    entries.append(entry)
            return entries, errors
        finally:
            db.close()

    def _resolve_one(
        self,
        repo: ProjectRepository,
        page: int,
        *,
        allow_unresolved_figures: bool,
    ) -> tuple[PageSourceEntry | None, str | None]:
        tr = repo.get_stage_state(page, PipelineStage.TRANSCRIBE)
        tr_status = (tr or {}).get("status")
        canonical = self.project_root / "markdown_pages" / f"page_{page:04d}.md"
        page_json = self.project_root / "page_results" / f"page_{page:04d}.json"
        # Prefer on-disk canonical over a missing/stale stage row
        if tr_status not in _OK_TRANSCRIBE:
            if canonical.exists() and page_json.exists():
                repo.upsert_stage_state(
                    page,
                    PipelineStage.TRANSCRIBE,
                    StageStatus.SUCCESS,
                    artifact_path=str(page_json),
                )
                tr_status = StageStatus.SUCCESS.value
            else:
                return None, f"page_{page:04d}_transcribe_not_ready:{tr_status}"

        fig_count = self._figure_count(page)
        fig_stage = repo.get_stage_state(page, PipelineStage.FIGURES)
        fig_status = (fig_stage or {}).get("status")

        resolved = self.project_root / "resolved_pages" / f"page_{page:04d}.md"

        if fig_count > 0:
            if fig_status not in _OK_FIGURES and not allow_unresolved_figures:
                return None, f"page_{page:04d}_figures_not_ready:{fig_status}"
            if resolved.exists():
                return (
                    PageSourceEntry(
                        page=page,
                        source=str(resolved.relative_to(self.project_root)).replace(
                            "\\", "/"
                        ),
                        source_type=PageSourceType.RESOLVED.value,
                        sha256=file_sha256(resolved),
                        figure_count=fig_count,
                    ),
                    None,
                )
            if allow_unresolved_figures and canonical.exists():
                return (
                    PageSourceEntry(
                        page=page,
                        source=str(canonical.relative_to(self.project_root)).replace(
                            "\\", "/"
                        ),
                        source_type=PageSourceType.CANONICAL.value,
                        sha256=file_sha256(canonical),
                        figure_count=fig_count,
                    ),
                    None,
                )
            return None, f"page_{page:04d}_missing_resolved"

        # No figures: prefer resolved copy if present and figures stage ok
        if resolved.exists() and (
            fig_status in _OK_FIGURES or fig_status is None or fig_status == StageStatus.WAITING.value
        ):
            # Prefer resolved when figures stage succeeded (even with 0 figures)
            if fig_status in _OK_FIGURES:
                return (
                    PageSourceEntry(
                        page=page,
                        source=str(resolved.relative_to(self.project_root)).replace("\\", "/"),
                        source_type=PageSourceType.RESOLVED.value,
                        sha256=file_sha256(resolved),
                        figure_count=0,
                    ),
                    None,
                )

        if not canonical.exists():
            return None, f"page_{page:04d}_missing_canonical"

        return (
            PageSourceEntry(
                page=page,
                source=str(canonical.relative_to(self.project_root)).replace("\\", "/"),
                source_type=PageSourceType.CANONICAL.value,
                sha256=file_sha256(canonical),
                figure_count=0,
            ),
            None,
        )

    def _figure_count(self, page: int) -> int:
        js = self.project_root / "page_results" / f"page_{page:04d}.json"
        if not js.exists():
            return 0
        try:
            payload = json.loads(js.read_text(encoding="utf-8"))
            figs = (payload.get("result") or {}).get("figures") or []
            return len(figs)
        except (json.JSONDecodeError, OSError):
            return 0

    def write_manifest(
        self, entries: list[PageSourceEntry], out_path: Path
    ) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "pages": [
                {
                    "page": e.page,
                    "source": e.source,
                    "source_type": e.source_type,
                    "sha256": e.sha256,
                    "figure_count": e.figure_count,
                }
                for e in entries
            ]
        }
        tmp = out_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        tmp.replace(out_path)
        return out_path
