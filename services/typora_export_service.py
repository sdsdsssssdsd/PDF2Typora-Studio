"""Export final.md + figures (+ optional source.pdf) for Typora."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.final_models import EXPORT_PIPELINE_VERSION
from storage.database import Database
from storage.repository import ProjectRepository
from utils.hashing import file_sha256, text_sha256
from utils.logger import get_logger

logger = get_logger("typora_export")

_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_FORBIDDEN_NAMES = {
    "project.db",
    "logs",
    "pages",
    "clean_pages",
    "experiments",
    "history",
    "page_results",
    "markdown_pages",
    "resolved_pages",
    "intermediate",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


@dataclass
class TyporaExportResult:
    success: bool
    status: str
    cached: bool = False
    export_path: Path | None = None
    markdown_path: Path | None = None
    export_hash: str = ""
    figure_count: int = 0
    include_source_pdf: bool = False
    source_pdf_hash: str = ""
    final_hash: str = ""
    error: str | None = None
    manifest_path: Path | None = None
    warnings: list[str] = field(default_factory=list)


class TyporaExportService:
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
        export_cfg = self.config.get("export") or {}
        self.pipeline_version = str(
            export_cfg.get("pipeline_version") or EXPORT_PIPELINE_VERSION
        )
        default_root = export_cfg.get("default_root") or "./exports"
        root = Path(default_root)
        if not root.is_absolute():
            from config.config_manager import project_root as app_root

            root = (app_root() / root).resolve()
        self.default_root = root
        self.include_source_pdf = bool(export_cfg.get("include_source_pdf", True))
        self.overwrite_existing = bool(export_cfg.get("overwrite_existing", False))
        self.backup_existing = bool(export_cfg.get("backup_existing", True))

    def export(
        self,
        *,
        export_root: Path | None = None,
        project_name: str | None = None,
        include_source_pdf: bool | None = None,
        overwrite: bool | None = None,
        force: bool = False,
        on_progress: Callable[[str], None] | None = None,
    ) -> TyporaExportResult:
        def prog(msg: str) -> None:
            if on_progress:
                on_progress(msg)

        include_pdf = (
            self.include_source_pdf
            if include_source_pdf is None
            else bool(include_source_pdf)
        )
        do_overwrite = (
            self.overwrite_existing if overwrite is None else bool(overwrite)
        )

        final_path = self.project_root / "final.md"
        if not final_path.exists():
            return TyporaExportResult(
                success=False, status="FAILED", error="final_md_missing"
            )
        clean_path = self.project_root / "intermediate" / "clean.md"
        if clean_path.exists() and file_sha256(clean_path) != file_sha256(final_path):
            return TyporaExportResult(
                success=False, status="FAILED", error="final_stale_vs_clean"
            )

        name = project_name or self._project_name()
        out_root = Path(export_root) if export_root else self.default_root
        out_root.mkdir(parents=True, exist_ok=True)
        target = out_root / name

        final_hash = file_sha256(final_path)
        figures = self._collect_figures(final_path)
        source_pdf = self._source_pdf()
        source_hash = ""
        if include_pdf:
            if source_pdf is None or not source_pdf.exists():
                return TyporaExportResult(
                    success=False, status="FAILED", error="source_pdf_missing"
                )
            source_hash = file_sha256(source_pdf)

        export_hash = self._compute_export_hash(
            final_hash=final_hash,
            figure_paths=figures,
            source_hash=source_hash,
            include_pdf=include_pdf,
        )

        if target.exists() and not force:
            existing_md = target / f"{name}.md"
            if existing_md.exists() and file_sha256(existing_md) == final_hash:
                if self._validate_export_dir(
                    target, name=name, final_hash=final_hash, include_pdf=include_pdf
                ):
                    prog("export up-to-date")
                    self._insert_run(
                        status="UP_TO_DATE",
                        project_name=name,
                        source_final_hash=final_hash,
                        export_path=str(target),
                        markdown_path=str(existing_md),
                        include_source_pdf=include_pdf,
                        figure_count=len(figures),
                        export_hash=export_hash,
                        error_message=None,
                    )
                    return TyporaExportResult(
                        success=True,
                        status="UP_TO_DATE",
                        cached=True,
                        export_path=target,
                        markdown_path=existing_md,
                        export_hash=export_hash,
                        figure_count=len(figures),
                        include_source_pdf=include_pdf,
                        source_pdf_hash=source_hash,
                        final_hash=final_hash,
                    )

        staging_root = out_root / ".staging" / uuid.uuid4().hex
        staging = staging_root / name
        try:
            prog("building staging export")
            staging.mkdir(parents=True)
            md_dest = staging / f"{name}.md"
            shutil.copyfile(final_path, md_dest)
            if file_sha256(md_dest) != final_hash:
                raise RuntimeError("exported_md_hash_mismatch")

            fig_dir = staging / "figures"
            fig_dir.mkdir(parents=True)
            for rel in figures:
                src = self.project_root / rel
                if not src.exists():
                    raise RuntimeError(f"missing_figure:{rel}")
                dest = staging / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)

            if include_pdf and source_pdf is not None:
                shutil.copy2(source_pdf, staging / "source.pdf")

            prog("validating staging")
            ok, err = self._validate_staging(
                staging,
                name=name,
                final_hash=final_hash,
                include_pdf=include_pdf,
            )
            if not ok:
                raise RuntimeError(err or "staging_validation_failed")

            # forbid workspace junk
            for bad in _FORBIDDEN_NAMES:
                if (staging / bad).exists():
                    raise RuntimeError(f"forbidden_export_entry:{bad}")

            prog("committing export")
            if target.exists():
                if not do_overwrite and not self.backup_existing:
                    # versioned directory
                    versioned = out_root / f"{name}_{_stamp()}"
                    shutil.move(str(staging), str(versioned))
                    target = versioned
                    md_dest = target / f"{name}.md"
                else:
                    if self.backup_existing:
                        backup = out_root / f"{name}_backup_{_stamp()}"
                        if backup.exists():
                            shutil.rmtree(backup)
                        shutil.move(str(target), str(backup))
                    else:
                        shutil.rmtree(target)
                    shutil.move(str(staging), str(target))
            else:
                shutil.move(str(staging), str(target))

            md_out = target / f"{name}.md"
            manifest = self._write_manifest(
                target=target,
                name=name,
                final_hash=final_hash,
                export_hash=export_hash,
                figure_count=len(figures),
                include_pdf=include_pdf,
                source_hash=source_hash,
            )
            self._insert_run(
                status="SUCCESS",
                project_name=name,
                source_final_hash=final_hash,
                export_path=str(target),
                markdown_path=str(md_out),
                include_source_pdf=include_pdf,
                figure_count=len(figures),
                export_hash=export_hash,
                error_message=None,
            )
            return TyporaExportResult(
                success=True,
                status="SUCCESS",
                cached=False,
                export_path=target,
                markdown_path=md_out,
                export_hash=export_hash,
                figure_count=len(figures),
                include_source_pdf=include_pdf,
                source_pdf_hash=source_hash,
                final_hash=final_hash,
                manifest_path=manifest,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("export failed")
            self._insert_run(
                status="FAILED",
                project_name=name,
                source_final_hash=final_hash,
                export_path=str(target),
                markdown_path=None,
                include_source_pdf=include_pdf,
                figure_count=len(figures),
                export_hash=export_hash,
                error_message=str(exc),
            )
            return TyporaExportResult(
                success=False,
                status="FAILED",
                error=str(exc),
                export_hash=export_hash,
                final_hash=final_hash,
                include_source_pdf=include_pdf,
            )
        finally:
            if staging_root.exists():
                shutil.rmtree(staging_root, ignore_errors=True)

    def _collect_figures(self, md_path: Path) -> list[str]:
        text = md_path.read_text(encoding="utf-8")
        seen: list[str] = []
        for _alt, target in _IMAGE_RE.findall(text):
            t = target.strip()
            if t.startswith("figures/") and t not in seen:
                seen.append(t)
        # also copy any extra files under figures/ that are referenced only?
        # Spec: only formal artifacts needed — stick to referenced
        return seen

    def _source_pdf(self) -> Path | None:
        local = self.project_root / "source.pdf"
        if local.exists():
            return local
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

    def _project_name(self) -> str:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            proj = repo.get_project()
            if proj and proj.get("name"):
                return str(proj["name"])
        finally:
            db.close()
        return self.project_root.name

    def _compute_export_hash(
        self,
        *,
        final_hash: str,
        figure_paths: list[str],
        source_hash: str,
        include_pdf: bool,
    ) -> str:
        parts = [final_hash, self.pipeline_version]
        for rel in figure_paths:
            p = self.project_root / rel
            parts.append(f"{rel}:{file_sha256(p) if p.exists() else 'missing'}")
        if include_pdf:
            parts.append(f"source:{source_hash}")
        return text_sha256("|".join(parts))

    def _validate_staging(
        self,
        staging: Path,
        *,
        name: str,
        final_hash: str,
        include_pdf: bool,
    ) -> tuple[bool, str | None]:
        md = staging / f"{name}.md"
        if not md.exists():
            return False, "markdown_missing"
        if file_sha256(md) != final_hash:
            return False, "markdown_hash_mismatch"
        text = md.read_text(encoding="utf-8")
        for _alt, target in _IMAGE_RE.findall(text):
            t = target.strip()
            if t.startswith(("http://", "https://", "file://")) or re.search(
                r"(?i)[A-Z]:\\", t
            ):
                return False, f"absolute_or_url_image:{t}"
            resolved = (staging / t).resolve()
            fig_root = (staging / "figures").resolve()
            try:
                resolved.relative_to(fig_root)
            except ValueError:
                return False, f"image_outside_figures:{t}"
            if not resolved.exists() or resolved.stat().st_size == 0:
                return False, f"missing_image:{t}"
        if include_pdf and not (staging / "source.pdf").exists():
            return False, "source_pdf_missing"
        return True, None

    def _validate_export_dir(
        self,
        target: Path,
        *,
        name: str,
        final_hash: str,
        include_pdf: bool,
    ) -> bool:
        ok, _ = self._validate_staging(
            target, name=name, final_hash=final_hash, include_pdf=include_pdf
        )
        return ok

    def _last_export_hash(self) -> str | None:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            row = repo.get_latest_export_run()
            return (row or {}).get("export_hash")
        finally:
            db.close()

    def _insert_run(self, **kwargs: Any) -> None:
        db = Database(self.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            repo.insert_export_run(**kwargs)
        finally:
            db.close()

    def _write_manifest(self, **kwargs: Any) -> Path:
        reports = self.project_root / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        path = reports / f"export_{_stamp()}.json"
        payload = {
            "exported_markdown": str(kwargs.get("target") / f"{kwargs['name']}.md"),
            "export_path": str(kwargs["target"]),
            "final_hash": kwargs["final_hash"],
            "export_hash": kwargs["export_hash"],
            "figure_count": kwargs["figure_count"],
            "include_source_pdf": kwargs["include_pdf"],
            "source_pdf_hash": kwargs.get("source_hash") or "",
            "pipeline_version": self.pipeline_version,
            "created_at": _now(),
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path
