"""Project creation tests."""

import tempfile
from pathlib import Path

import pymupdf
import pytest

from services.project_service import ProjectService


def _make_sample_pdf(path: Path, pages: int = 3) -> None:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1}")
    doc.save(str(path))
    doc.close()


def test_create_project_from_pdf():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "workspace"
        pdf_path = Path(tmp) / "sample.pdf"
        _make_sample_pdf(pdf_path, pages=3)

        svc = ProjectService(workspace_root=ws)
        project = svc.create_project(pdf_path)

        assert project.info.source_pdf.exists()
        assert project.db_path.exists()
        assert project.pages_dir.is_dir()
        assert project.figures_dir.is_dir()
        assert (project.root / "project.json").exists()
        assert project.info.page_count == 3
        assert project.pdf_hash()

        from storage.database import Database
        from storage.repository import ProjectRepository

        db = Database(project.db_path)
        repo = ProjectRepository(db)
        assert len(repo.list_pages()) == 3
        assert len(repo.list_stage_states("render")) == 3
        db.close()


def test_inspect_pdf_not_found():
    svc = ProjectService(workspace_root=Path(tempfile.mkdtemp()))
    with pytest.raises(Exception):
        svc.inspect_pdf(Path("/nonexistent/file.pdf"))
