"""Phase 6.5 tests — marker repair, review service, readiness."""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from config.config_manager import load_config
from core.figure_models import FigureRequest
from core.models import PipelineStage, StageStatus
from services.figure_marker_normalizer import FigureMarkerNormalizer, canonical_marker
from services.figure_marker_validator import FigureMarkerValidator
from services.figure_review_service import FigureReviewService, validate_bbox_1000
from services.figure_readiness_service import FigureReadinessService
from services.resolved_page_builder import (
    ManualMarkerPlacement,
    MarkerRepairRecord,
    ResolvedPageBuilder,
    ResolvedPageInput,
)
from storage.database import CURRENT_SCHEMA_VERSION, Database
from storage.repository import ProjectRepository
from utils.hashing import file_sha256


def _write_canonical(root: Path, page: int, markdown: str, figures: list[dict]) -> None:
    md_dir = root / "markdown_pages"
    js_dir = root / "page_results"
    md_dir.mkdir(parents=True, exist_ok=True)
    js_dir.mkdir(parents=True, exist_ok=True)
    body = f"<!-- PAGE: {page:04d} -->\n\n{markdown}"
    (md_dir / f"page_{page:04d}.md").write_text(body, encoding="utf-8")
    payload = {
        "result": {
            "page_number": page,
            "markdown": markdown,
            "figures": figures,
            "warnings": [],
        },
        "provenance": {},
    }
    (js_dir / f"page_{page:04d}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _make_pdf(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 80, 60), 1)
    pix.clear_with(255)
    page.insert_image(pymupdf.Rect(100, 80, 220, 170), pixmap=pix)
    doc.save(str(path))
    doc.close()


def test_schema_v6_migration(tmp_path: Path):
    db_path = tmp_path / "m.db"
    db = Database(db_path)
    db.initialize()
    assert db.get_schema_version() == CURRENT_SCHEMA_VERSION
    db.close()


def test_loose_marker_normalization():
    norm = FigureMarkerNormalizer()
    md = "A\n\nFIGURE page=4 index=1\n\nB"
    markers = norm.find_markers(md)
    assert len(markers) == 1
    assert markers[0].index == 1
    assert markers[0].page == 4
    assert not markers[0].is_strict
    repaired = norm.apply_repairs(md, markers)
    assert "<!-- FIGURE page=4 index=1 -->" in repaired
    assert all(m.is_strict for m in norm.find_markers(repaired))


def test_strict_marker_passes():
    norm = FigureMarkerNormalizer()
    md = "<!-- FIGURE page=4 index=1 -->"
    m = norm.find_markers(md)
    assert len(m) == 1
    assert m[0].is_strict


def test_missing_marker_not_auto_inserted():
    v = FigureMarkerValidator()
    md = "正文无 marker"
    req = [
        FigureRequest(
            page_number=4,
            figure_index=1,
            marker="FIGURE page=4 index=1",
            figure_type="chart",
            caption=None,
            ai_bbox_1000=None,
        )
    ]
    r = v.validate(page_number=4, markdown=md, requests=req)
    assert "missing_marker" in r.issues
    assert not r.safe_marker_fix
    assert not r.safe_repairs


def test_index_conflict_not_auto_fixed():
    v = FigureMarkerValidator()
    md = "FIGURE page=4 index=7"
    req = [
        FigureRequest(
            page_number=4,
            figure_index=1,
            marker="FIGURE page=4 index=1",
            figure_type="chart",
            caption=None,
            ai_bbox_1000=None,
        )
    ]
    r = v.validate(page_number=4, markdown=md, requests=req)
    assert "marker_index_conflict" in r.issues
    assert not r.safe_marker_fix


def test_safe_syntax_repair_when_indices_align():
    v = FigureMarkerValidator()
    md = "A\nFIGURE page=4 index=1\nB"
    req = [
        FigureRequest(
            page_number=4,
            figure_index=1,
            marker="FIGURE page=4 index=1",
            figure_type="chart",
            caption=None,
            ai_bbox_1000=(200, 200, 600, 600),
        )
    ]
    r = v.validate(page_number=4, markdown=md, requests=req)
    assert r.safe_marker_fix
    assert len(r.safe_repairs) == 1


def test_canonical_sha256_unchanged_after_repair_build(tmp_path: Path):
    canon = "Intro\nFIGURE page=1 index=1\nOutro"
    builder = ResolvedPageBuilder(tmp_path / "resolved")
    md, _ = builder.build(
        ResolvedPageInput(
            page_number=1,
            canonical_md=canon,
            marker_repairs=[
                MarkerRepairRecord(
                    figure_index=1,
                    original="FIGURE page=1 index=1",
                    normalized=canonical_marker(1, 1),
                )
            ],
            figure_paths={1: "figures/p0001_fig01.png"},
            figure_hashes={1: "abc123"},
        )
    )
    assert "![图]" in md
    assert canon == "Intro\nFIGURE page=1 index=1\nOutro"


def test_preview_does_not_write_figures_dir(tmp_path: Path):
    pdf = tmp_path / "t.pdf"
    _make_pdf(pdf)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "figures").mkdir()
    (root / "pages").mkdir()
    db_path = root / "p.db"
    db = Database(db_path)
    db.initialize()
    repo = ProjectRepository(db)
    repo.insert_project("t", str(pdf), 1)
    repo.init_pages(1)
    db.close()

    _write_canonical(
        root,
        1,
        "Text\n<!-- FIGURE page=1 index=1 -->\n",
        [{"figure_index": 1, "figure_type": "figure", "marker": "<!-- FIGURE page=1 index=1 -->", "bbox_1000": [250, 250, 750, 750]}],
    )
    page_png = root / "pages" / "page_0001.png"
    doc = pymupdf.open(str(pdf))
    pix = doc[0].get_pixmap(dpi=150)
    pix.save(str(page_png))
    doc.close()

    svc = FigureReviewService(
        project_root=root,
        pdf_path=pdf,
        db_path=db_path,
        pdf_hash=file_sha256(pdf),
    )
    before = list((root / "figures").glob("*"))
    preview = svc.generate_preview(
        page_number=1,
        figure_index=1,
        bbox_1000=(250, 250, 750, 750),
    )
    after = list((root / "figures").glob("*"))
    assert preview.exists()
    assert before == after


def test_readiness_gate(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    db_path = root / "p.db"
    db = Database(db_path)
    db.initialize()
    repo = ProjectRepository(db)
    repo.insert_project("t", str(root / "x.pdf"), 1)
    repo.init_pages(1)
    repo.upsert_stage_state(1, PipelineStage.TRANSCRIBE, StageStatus.SUCCESS)
    repo.upsert_figure(
        page_number=1,
        figure_index=1,
        status="needs_review",
    )
    db.close()
    _write_canonical(root, 1, "x", [{"figure_index": 1, "figure_type": "f", "marker": "m"}])

    # Strict mode (legacy): needs_review blocks assemble
    strict = {
        "figures": {
            "caption_anchored_auto": False,
            "readiness": {
                "require_all_resolved": True,
                "allow_unresolved_override": False,
            },
        }
    }
    svc = FigureReadinessService(project_root=root, db_path=db_path, config=strict)
    assert not svc.is_ready_for_assemble()

    # Caption-anchored: stale needs_review must not trap the user
    auto = {
        "figures": {
            "caption_anchored_auto": True,
            "readiness": {
                "require_all_resolved": False,
                "allow_unresolved_override": True,
            },
        }
    }
    svc2 = FigureReadinessService(project_root=root, db_path=db_path, config=auto)
    assert svc2.is_ready_for_assemble()


def test_manual_marker_insertion_build(tmp_path: Path):
    canon = "Para A\n\nPara B"
    builder = ResolvedPageBuilder(tmp_path / "resolved")
    md, _ = builder.build(
        ResolvedPageInput(
            page_number=1,
            canonical_md=canon,
            manual_placements=[
                ManualMarkerPlacement(
                    figure_index=1,
                    page_number=1,
                    char_offset=7,
                    before_context="Para A",
                    after_context="\n\nPara B",
                )
            ],
            figure_paths={1: "figures/p0001_fig01.png"},
            figure_hashes={1: "h1"},
        )
    )
    assert "![图]" in md
    assert canon == "Para A\n\nPara B"


def test_bbox_validation():
    assert validate_bbox_1000((10, 10, 5, 20))
    assert validate_bbox_1000((0, 0, 5, 5))
