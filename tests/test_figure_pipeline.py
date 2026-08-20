"""Figure pipeline unit tests with generated PDFs."""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from ai.schemas.transcription import FigureDetection, PageTranscriptionResult
from config.config_manager import load_config
from core.models import PipelineStage, StageStatus
from services.figure_marker_validator import FigureMarkerValidator
from services.figure_matcher import FigureMatcher
from services.figure_resolver import FigureResolver, marker_to_image_md
from services.figure_service import FigureService
from storage.database import CURRENT_SCHEMA_VERSION, Database
from storage.repository import ProjectRepository
from utils.geometry import bbox_1000_to_page_rect, iou, page_rect_to_bbox_1000
from utils.hashing import file_sha256


def _write_canonical(
    root: Path, page: int, markdown: str, figures: list[FigureDetection]
) -> None:
    md_dir = root / "markdown_pages"
    js_dir = root / "page_results"
    md_dir.mkdir(parents=True, exist_ok=True)
    js_dir.mkdir(parents=True, exist_ok=True)
    body = f"<!-- PAGE: {page:04d} -->\n\n{markdown}"
    (md_dir / f"page_{page:04d}.md").write_text(body, encoding="utf-8")
    result = PageTranscriptionResult(page_number=page, markdown=markdown, figures=figures)
    payload = {"result": result.model_dump(), "provenance": {}}
    (js_dir / f"page_{page:04d}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _make_pdf_single_image(path: Path, rotation: int = 0) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    if rotation:
        page.set_rotation(rotation)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 80, 60), 1)
    pix.clear_with(255)
    rect = pymupdf.Rect(100, 80, 220, 170)
    page.insert_image(rect, pixmap=pix)
    doc.save(str(path))
    doc.close()


def _make_pdf_two_images(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=500, height=400)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), 1)
    pix.clear_with(200)
    page.insert_image(pymupdf.Rect(50, 50, 150, 150), pixmap=pix)
    page.insert_image(pymupdf.Rect(300, 200, 420, 320), pixmap=pix)
    doc.save(str(path))
    doc.close()


def _setup_project(tmp_path: Path, pdf_path: Path, page: int = 1) -> tuple[Path, Path]:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "figures").mkdir()
    (root / "resolved_pages").mkdir()
    (root / "pages").mkdir()
    db_path = root / "project.db"
    db = Database(db_path)
    db.initialize()
    assert db.get_schema_version() == CURRENT_SCHEMA_VERSION
    repo = ProjectRepository(db)
    repo.insert_project("t", str(pdf_path), 1)
    repo.init_pages(1)
    repo.upsert_stage_state(page, PipelineStage.RENDER, StageStatus.SUCCESS)
    repo.upsert_stage_state(page, PipelineStage.TRANSCRIBE, StageStatus.SUCCESS)
    db.close()
    return root, db_path


def test_schema_v5_migration(tmp_path: Path):
    db_path = tmp_path / "m.db"
    db = Database(db_path)
    db.initialize()
    assert db.get_schema_version() == CURRENT_SCHEMA_VERSION
    db.close()


def test_native_candidate_discovery(tmp_path: Path):
    pdf = tmp_path / "one.pdf"
    _make_pdf_single_image(pdf)
    doc = pymupdf.open(str(pdf))
    page = doc[0]
    from services.figure_candidate_service import FigureCandidateService

    cands = FigureCandidateService(load_config().get("figures", {})).discover(page, 1)
    doc.close()
    raster = [c for c in cands if c.candidate_type == "raster"]
    assert len(raster) >= 1
    assert raster[0].xref
    assert raster[0].digest


def test_repeated_image_two_occurrences(tmp_path: Path):
    pdf = tmp_path / "two.pdf"
    _make_pdf_two_images(pdf)
    doc = pymupdf.open(str(pdf))
    from services.figure_candidate_service import FigureCandidateService

    cands = FigureCandidateService({}).discover(doc[0], 1)
    doc.close()
    raster = [c for c in cands if c.candidate_type == "raster"]
    assert len(raster) == 2


def test_matching_high_overlap():
    matcher = FigureMatcher({"matching": {"auto_threshold": 0.85, "review_threshold": 0.55}})
    from core.figure_models import FigureCandidate, FigureRequest

    req = FigureRequest(
        page_number=1,
        figure_index=1,
        marker="<!-- FIGURE page=1 index=1 -->",
        figure_type="figure",
        caption=None,
        ai_bbox_1000=(200, 250, 600, 750),
    )
    cand = FigureCandidate(
        candidate_id="r0",
        page_number=1,
        candidate_type="raster",
        bbox_pdf=None,
        bbox_1000=(210, 260, 590, 740),
    )
    m = matcher.match(req, [cand], marker_ok=True)
    assert m.score > 0.7


def test_ambiguous_two_candidates():
    matcher = FigureMatcher({"matching": {"auto_threshold": 0.85, "review_threshold": 0.55}})
    from core.figure_models import FigureCandidate, FigureRequest

    req = FigureRequest(
        page_number=1,
        figure_index=1,
        marker="<!-- FIGURE page=1 index=1 -->",
        figure_type="figure",
        caption=None,
        ai_bbox_1000=(400, 400, 600, 600),
    )
    c1 = FigureCandidate("a", 1, "raster", None, (380, 380, 620, 620))
    c2 = FigureCandidate("b", 1, "raster", None, (390, 390, 610, 610))
    m = matcher.match(req, [c1, c2], marker_ok=True)
    assert not m.auto_resolvable or "ambiguous_candidate" in m.reasons


def test_marker_mismatch_needs_review():
    v = FigureMarkerValidator()
    from core.figure_models import FigureRequest

    req = FigureRequest(
        page_number=4,
        figure_index=1,
        marker="<!-- FIGURE page=4 index=1 -->",
        figure_type="chart",
        caption=None,
        ai_bbox_1000=(100, 100, 500, 500),
    )
    report = v.validate(page_number=4, markdown="no marker here", requests=[req])
    assert report.needs_review
    assert "missing_marker" in report.issues


def test_extra_marker_needs_review():
    v = FigureMarkerValidator()
    from core.figure_models import FigureRequest

    req = FigureRequest(
        page_number=1,
        figure_index=1,
        marker="<!-- FIGURE page=1 index=1 -->",
        figure_type="figure",
        caption=None,
        ai_bbox_1000=None,
    )
    md = "A\n<!-- FIGURE page=1 index=1 -->\n<!-- FIGURE page=1 index=2 -->\n"
    report = v.validate(page_number=1, markdown=md, requests=[req])
    assert report.needs_review
    assert "extra_marker" in report.issues


def test_resolved_markdown_replacement():
    md = "Text\n\n<!-- FIGURE page=4 index=1 -->\n\nMore"
    resolved, _ = FigureResolver(Path(".")).resolve_page(
        page_number=4,
        canonical_md=md,
        figure_paths={1: "figures/p0004_fig01.png"},
        figure_hashes={1: "abc"},
    )
    assert "![图](figures/p0004_fig01.png)" in resolved
    assert "<!-- FIGURE" not in resolved
    assert marker_to_image_md(4, 1) == "![图](figures/p0004_fig01.png)"


def test_canonical_unchanged_after_pipeline(tmp_path: Path):
    pdf = tmp_path / "img.pdf"
    _make_pdf_single_image(pdf)
    root, db_path = _setup_project(tmp_path, pdf)
    bbox = (200, 200, 700, 700)
    md = "Hello\n\n<!-- FIGURE page=1 index=1 -->\n"
    fig = FigureDetection(
        figure_index=1,
        marker="<!-- FIGURE page=1 index=1 -->",
        figure_type="figure",
        bbox_1000=bbox,
    )
    _write_canonical(root, 1, md, [fig])
    canon = root / "markdown_pages" / "page_0001.md"
    before = file_sha256(canon)
    svc = FigureService(
        project_root=root,
        pdf_path=pdf,
        db_path=db_path,
        pdf_hash=file_sha256(pdf),
        config=load_config(),
    )
    svc.process_page(1)
    after = file_sha256(canon)
    assert before == after


def test_no_figure_page_copies_resolved(tmp_path: Path):
    pdf = tmp_path / "plain.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(pdf))
    doc.close()
    root, db_path = _setup_project(tmp_path, pdf)
    _write_canonical(root, 1, "Plain text only.", [])
    svc = FigureService(project_root=root, pdf_path=pdf, db_path=db_path, config=load_config())
    result = svc.process_page(1)
    assert result.stage_status == StageStatus.SUCCESS.value
    resolved = root / "resolved_pages" / "page_0001.md"
    assert resolved.exists()


def test_page4_marker_mismatch_review(tmp_path: Path):
    pdf = tmp_path / "p4.pdf"
    _make_pdf_single_image(pdf)
    root, db_path = _setup_project(tmp_path, pdf)
    fig = FigureDetection(
        figure_index=1,
        marker="<!-- FIGURE page=4 index=1 -->",
        figure_type="chart",
        bbox_1000=(100, 100, 800, 800),
    )
    _write_canonical(root, 1, "Body without marker", [fig])
    svc = FigureService(project_root=root, pdf_path=pdf, db_path=db_path, config=load_config())
    result = svc.process_page(1)
    assert result.stage_status == StageStatus.NEEDS_REVIEW.value
    assert result.figures[0]["status"] == "needs_review"


def test_rotated_page_bbox_conversion(tmp_path: Path):
    pdf_path = tmp_path / "rot.pdf"
    _make_pdf_single_image(pdf_path, rotation=90)
    doc = pymupdf.open(str(pdf_path))
    page = doc[0]
    bbox = (100, 100, 500, 500)
    rect = bbox_1000_to_page_rect(bbox, page)
    back = page_rect_to_bbox_1000(rect, page)
    assert iou(bbox, back) > 0.5
    doc.close()
