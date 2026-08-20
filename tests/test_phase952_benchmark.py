"""Phase 9.5.2 document engine benchmark unit tests."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from ai.document_parsers.native_pdf_provider import NativePdfProvider
from ai.document_parsers.registry import list_engines
from core.document_page_model import DocumentPageEvidence
from services.document_engine_benchmark import (
    DocumentEngineBenchmark,
    missing_text_ratio,
    score_against_reference,
)
from services.evidence_fusion import document_page_to_manifest


def _sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "bench.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=500)
    page.insert_text((72, 72), "Experimental Results", fontsize=16)
    page.insert_text((72, 110), "The experiment used 256 samples.", fontsize=11)
    page.insert_text((72, 140), "Fig. 1 Overview of the pipeline.", fontsize=11)
    doc.save(path)
    doc.close()
    return path


def test_list_engines_includes_native():
    ids = {e["id"] for e in list_engines()}
    assert "native_pdf" in ids
    assert "mineru" in ids
    assert "marker" in ids
    assert "docling" in ids
    assert "chandra" in ids
    native = next(e for e in list_engines() if e["id"] == "native_pdf")
    assert native["available"] is True


def test_native_provider_analyze(tmp_path: Path):
    pdf = _sample_pdf(tmp_path)
    ev = NativePdfProvider().analyze_page(pdf, 1)
    assert ev.ok
    assert ev.engine == "native_pdf"
    assert ev.plain_text
    assert ev.blocks


def test_missing_text_ratio():
    assert missing_text_ratio("hello world samples", "hello world samples") == 0.0
    assert missing_text_ratio("hello world samples", "") > 0.5


def test_benchmark_native_only(tmp_path: Path):
    pdf = _sample_pdf(tmp_path)
    report = DocumentEngineBenchmark().run(
        pdf_path=pdf,
        pages=[1],
        engines=["native_pdf", "mineru"],
    )
    assert len(report.rows) == 2
    native = next(r for r in report.rows if r.engine == "native_pdf")
    assert native.ok
    mineru = next(r for r in report.rows if r.engine == "mineru")
    # typically not installed in CI
    assert mineru.engine == "mineru"
    md = report.to_markdown_table()
    assert "native_pdf" in md
    paths = DocumentEngineBenchmark().write_report(report, tmp_path / "out")
    assert paths["md"].exists()
    assert paths["json"].exists()


def test_evidence_fusion(tmp_path: Path):
    pdf = _sample_pdf(tmp_path)
    ev = NativePdfProvider().analyze_page(pdf, 1)
    manifest = document_page_to_manifest(ev, pdf_hash="abc")
    assert manifest.page_number == 1
    assert manifest.blocks
    payload = manifest.reconstruction_payload()
    assert payload["page"] == 1


def test_score_unavailable():
    ev = DocumentPageEvidence(
        page_number=1, engine="chandra", ok=False, installed=False, error="x"
    )
    row = score_against_reference(ev, None)
    assert row.ok is False
    assert row.error == "x"
