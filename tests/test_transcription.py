"""Transcription schema and validator unit tests (no live Ollama)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai.schemas.transcription import FigureDetection, PageTranscriptionResult
from services.transcription_validator import TranscriptionValidator
from storage.database import CURRENT_SCHEMA_VERSION, Database
from storage.repository import ProjectRepository


def test_schema_roundtrip():
    result = PageTranscriptionResult(
        page_number=1,
        markdown="## Title\n\nHello $x$ world.",
        figures=[
            FigureDetection(
                figure_index=1,
                marker="<!-- FIGURE page=1 index=1 -->",
                figure_type="plot",
            )
        ],
    )
    raw = result.model_dump_json()
    again = PageTranscriptionResult.model_validate_json(raw)
    assert again.page_number == 1
    schema = PageTranscriptionResult.model_json_schema()
    assert "markdown" in schema["properties"]


def test_validator_figure_consistency():
    v = TranscriptionValidator()
    good = PageTranscriptionResult(
        page_number=3,
        markdown="Text\n\n<!-- FIGURE page=3 index=1 -->\n",
        figures=[
            FigureDetection(
                figure_index=1,
                marker="<!-- FIGURE page=3 index=1 -->",
                figure_type="chart",
            )
        ],
    )
    report = v.validate(good, requested_page=3)
    assert report.ok
    assert not any("missing" in w for w in report.warnings)

    bad = PageTranscriptionResult(
        page_number=3,
        markdown="no marker",
        figures=[
            FigureDetection(
                figure_index=1,
                marker="<!-- FIGURE page=3 index=1 -->",
                figure_type="chart",
            )
        ],
    )
    report2 = v.validate(bad, requested_page=3)
    assert report2.ok  # schema still valid
    assert report2.needs_review
    assert any(i.code == "figure_marker_mismatch" for i in report2.blocking)


def test_validator_rejects_empty_markdown():
    v = TranscriptionValidator()
    r = PageTranscriptionResult(page_number=1, markdown="   ")
    report = v.validate(r, requested_page=1)
    assert not report.ok


def test_schema_v3_migration(tmp_path: Path):
    db_path = tmp_path / "p.db"
    db = Database(db_path)
    db.initialize()
    assert db.get_schema_version() == CURRENT_SCHEMA_VERSION
    assert CURRENT_SCHEMA_VERSION == 10
    repo = ProjectRepository(db)
    rid = repo.insert_ai_request(
        page_number=1,
        provider="ollama",
        model="gemma3:4b",
        model_digest="abc",
        request_hash="hash",
        status="RUNNING",
    )
    repo.update_ai_request(request_id=rid, status="SUCCESS", total_duration_ns=123)
    db.close()


def test_invalid_bbox_rejected_by_pydantic():
    with pytest.raises(Exception):
        FigureDetection(
            figure_index=1,
            marker="<!-- FIGURE page=1 index=1 -->",
            bbox_1000=(0, 0, 2000, 10),
        )


def test_schema_forbids_extra_fields():
    with pytest.raises(Exception):
        PageTranscriptionResult.model_validate(
            {
                "page_number": 1,
                "markdown": "ok",
                "invented": True,
            }
        )


def test_prompt_leak_detector():
    from services.prompt_leak_detector import is_prompt_leak

    clean = "This page discusses student performance datasets."
    assert not is_prompt_leak(clean)
    leaked = (
        "The attached image is the only source document. "
        "Never output markdown image syntax. Hello."
    )
    assert is_prompt_leak(leaked)


def test_image_url_blocks_auto_accept():
    v = TranscriptionValidator()
    r = PageTranscriptionResult(
        page_number=1,
        markdown="See ![x](http://example.com/a.png)",
    )
    report = v.validate(r, requested_page=1)
    assert any(i.code == "invented_image_reference" for i in report.blocking)
    assert not report.can_auto_accept(r)


def test_auto_accept_clean_page():
    v = TranscriptionValidator()
    r = PageTranscriptionResult(
        page_number=2,
        markdown="Plain paragraph about the data set.",
        needs_review=False,
    )
    report = v.validate(r, requested_page=2)
    assert report.can_auto_accept(r)


def test_figure_marker_pass():
    v = TranscriptionValidator()
    r = PageTranscriptionResult(
        page_number=4,
        markdown="Text\n\n<!-- FIGURE page=4 index=1 -->\n",
        figures=[
            FigureDetection(
                figure_index=1,
                marker="<!-- FIGURE page=4 index=1 -->",
                figure_type="chart",
            )
        ],
    )
    report = v.validate(r, requested_page=4)
    assert report.ok
    assert not any(i.code == "figure_marker_mismatch" for i in report.blocking)
