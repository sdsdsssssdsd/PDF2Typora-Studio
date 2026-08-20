"""Render service tests: cache, DPI invalidation, rotation, migration."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from core.models import (
    PipelineStage,
    RenderRequest,
    RenderSettings,
    StageStatus,
)
from services.project_service import ProjectService
from services.render_service import RenderService
from storage.database import CURRENT_SCHEMA_VERSION, Database
from storage.repository import ProjectRepository
from utils.hashing import file_sha256


def _make_pdf(path: Path, pages: int = 3, rotate_last: bool = False) -> None:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=300, height=400)
        page.insert_text((40, 80), f"Page {i + 1}")
        if rotate_last and i == pages - 1:
            page.set_rotation(90)
    doc.save(str(path))
    doc.close()


def test_database_migration_adds_stage_table(tmp_path: Path):
    db_path = tmp_path / "old.db"
    db = Database(db_path)
    conn = db.connect()
    conn.execute(
        """
        CREATE TABLE pages (
            id INTEGER PRIMARY KEY,
            page_number INTEGER,
            status TEXT,
            retry_count INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.commit()
    db.initialize()
    assert db.get_schema_version() == CURRENT_SCHEMA_VERSION
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "page_stage_states" in tables
    db.close()


def test_render_and_cache_hit(tmp_path: Path):
    pdf_path = tmp_path / "book.pdf"
    _make_pdf(pdf_path, pages=3)
    svc = ProjectService(workspace_root=tmp_path / "ws")
    project = svc.create_project(pdf_path)
    pdf_hash = file_sha256(project.info.source_pdf)

    request = RenderRequest(
        pdf_path=project.info.source_pdf,
        output_dir=project.pages_dir,
        pages=(1, 2, 3),
        settings=RenderSettings(dpi=200),
        pdf_hash=pdf_hash,
        db_path=project.db_path,
    )
    service = RenderService()
    first = service.render_pages(request)
    assert all(r.success and not r.cached for r in first)
    for n in (1, 2, 3):
        assert (project.pages_dir / f"page_{n:04d}.png").exists()

    second = service.render_pages(request)
    assert all(r.success and r.cached for r in second)

    db = Database(project.db_path)
    repo = ProjectRepository(db)
    counts = repo.count_stage_by_status(PipelineStage.RENDER)
    assert counts.get(StageStatus.CACHED.value, 0) == 3
    db.close()


def test_dpi_change_invalidates_cache(tmp_path: Path):
    pdf_path = tmp_path / "book.pdf"
    _make_pdf(pdf_path, pages=1)
    svc = ProjectService(workspace_root=tmp_path / "ws")
    project = svc.create_project(pdf_path)
    pdf_hash = file_sha256(project.info.source_pdf)
    service = RenderService()
    req200 = RenderRequest(
        pdf_path=project.info.source_pdf,
        output_dir=project.pages_dir,
        pages=(1,),
        settings=RenderSettings(dpi=200),
        pdf_hash=pdf_hash,
        db_path=project.db_path,
    )
    r1 = service.render_pages(req200)[0]
    req300 = RenderRequest(
        pdf_path=project.info.source_pdf,
        output_dir=project.pages_dir,
        pages=(1,),
        settings=RenderSettings(dpi=300),
        pdf_hash=pdf_hash,
        db_path=project.db_path,
    )
    r2 = service.render_pages(req300)[0]
    assert r1.success and not r1.cached
    assert r2.success and not r2.cached
    assert r2.settings_hash != r1.settings_hash
    assert r2.width_px and r1.width_px and r2.width_px > r1.width_px


def test_rotated_page_renders(tmp_path: Path):
    pdf_path = tmp_path / "rot.pdf"
    _make_pdf(pdf_path, pages=2, rotate_last=True)
    out = tmp_path / "pages"
    out.mkdir()
    service = RenderService()
    req = RenderRequest(
        pdf_path=pdf_path,
        output_dir=out,
        pages=(1, 2),
        settings=RenderSettings(dpi=150),
        pdf_hash=file_sha256(pdf_path),
    )
    results = service.render_pages(req)
    assert all(r.success for r in results)
    # Rotated 90° page swaps visual width/height vs unrotated
    assert results[0].width_px != results[1].width_px or results[0].height_px != results[1].height_px


def test_single_page_failure_continues(tmp_path: Path):
    pdf_path = tmp_path / "book.pdf"
    _make_pdf(pdf_path, pages=2)
    service = RenderService()
    req = RenderRequest(
        pdf_path=pdf_path,
        output_dir=tmp_path / "pages",
        pages=(1, 99),
        settings=RenderSettings(dpi=150),
        pdf_hash=file_sha256(pdf_path),
    )
    results = service.render_pages(req)
    assert results[0].success
    assert not results[1].success
