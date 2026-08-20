"""Project creation and lifecycle management."""

from __future__ import annotations

import shutil
from pathlib import Path

from config.config_manager import load_config
from core.exceptions import ProjectError
from core.models import PDFInfo, ProjectInfo
from core.project import Project
from services.pdf_service import PDFService
from services.render_service import cleanup_render_temp_files
from storage.database import Database
from storage.repository import ProjectRepository
from utils.hashing import file_sha256
from utils.logger import get_logger
from utils.paths import ensure_dir, sanitize_book_name

logger = get_logger("project_service")


class ProjectService:
    def __init__(self, workspace_root: Path | None = None) -> None:
        config = load_config()
        ws = workspace_root or Path(config["workspace"]["path"])
        self.workspace_root = ensure_dir(ws)
        self._pdf_service = PDFService()

    def inspect_pdf(self, pdf_path: Path) -> PDFInfo:
        return self._pdf_service.inspect(pdf_path)

    def create_project(self, pdf_path: Path) -> Project:
        pdf_info = self.inspect_pdf(pdf_path)
        book_name = sanitize_book_name(pdf_info.file_name)
        project_root = self._unique_project_dir(book_name)

        logger.info("Creating project at %s", project_root)

        ensure_dir(project_root)
        project = Project(
            ProjectInfo(
                name=book_name,
                root_path=project_root,
                source_pdf=project_root / "source.pdf",
                db_path=project_root / "project.db",
                page_count=pdf_info.page_count,
                metadata={
                    "original_path": str(pdf_info.file_path),
                    "file_size": pdf_info.file_size,
                    "pdf_metadata": pdf_info.metadata,
                },
            )
        )

        project.ensure_directories()
        shutil.copy2(pdf_info.file_path, project.info.source_pdf)
        pdf_hash = file_sha256(project.info.source_pdf)
        project.info.metadata["pdf_hash"] = pdf_hash

        db = Database(project.db_path)
        try:
            db.initialize()
            repo = ProjectRepository(db)
            repo.insert_project(
                name=book_name,
                source_path=str(project.info.source_pdf),
                page_count=pdf_info.page_count,
            )
            repo.init_pages(pdf_info.page_count)
        finally:
            db.close()

        project.save_manifest()
        logger.info("Project created: %s (%d pages)", book_name, pdf_info.page_count)
        return project

    def open_project(self, project_root: Path) -> Project:
        if not project_root.is_dir():
            raise ProjectError(f"Project directory not found: {project_root}")
        project = Project.load_from_directory(project_root)
        db = Database(project.db_path)
        try:
            db.initialize()
            repo = ProjectRepository(db)
            repo.ensure_render_stages(project.info.page_count)
        finally:
            db.close()
        cleanup_render_temp_files(project.pages_dir)
        if not project.pdf_hash() and project.info.source_pdf.exists():
            project.set_pdf_hash(file_sha256(project.info.source_pdf))
        return project

    def list_projects(self) -> list[Path]:
        if not self.workspace_root.exists():
            return []
        return sorted(
            p for p in self.workspace_root.iterdir()
            if p.is_dir() and (p / Project.PROJECT_JSON).exists()
        )

    def _unique_project_dir(self, base_name: str) -> Path:
        candidate = self.workspace_root / base_name
        if not candidate.exists():
            return candidate
        n = 2
        while True:
            alt = self.workspace_root / f"{base_name}_{n}"
            if not alt.exists():
                return alt
            n += 1
