"""Phase 9.5.1 hybrid evidence + reconstruction unit tests."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from core.evidence_models import PageTextSourceMode
from services.markdown_reconstruction_service import MarkdownReconstructionService
from services.page_evidence_builder import PageEvidenceBuilder, choose_text_source_mode


def _sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=500)
    page.insert_text((72, 72), "3. Experimental Results", fontsize=16)
    page.insert_text((72, 110), "The experiment used 256 samples.", fontsize=11)
    page.insert_text((72, 140), "Fig. 1 Overview of the pipeline.", fontsize=11)
    doc.save(path)
    doc.close()
    return path


def test_choose_mode_native_plus_ocr():
    mode = choose_text_source_mode(
        pdf_text="The experiment used 256 samples. " * 3,
        ocr_text="The experiment used 256 samples.",
        ocr_ok=True,
    )
    assert mode == PageTextSourceMode.PDF_NATIVE_PLUS_OCR


def test_choose_mode_ocr_when_pdf_empty():
    mode = choose_text_source_mode(pdf_text="", ocr_text="hello world text", ocr_ok=True)
    assert mode in {
        PageTextSourceMode.OCR_PRIMARY,
        PageTextSourceMode.OCR_ONLY,
    }


def test_evidence_builder_pdf_native(tmp_path: Path):
    pdf = _sample_pdf(tmp_path)
    manifest = PageEvidenceBuilder().build(
        pdf_path=pdf, page_number=1, page_image=None, run_ocr=False
    )
    assert manifest.page_number == 1
    assert manifest.pdf_plain_text
    assert any(b.type in {"heading", "paragraph"} for b in manifest.blocks)
    assert "1" in manifest.figure_labels or any(
        "Fig" in (b.text or "") for b in manifest.blocks
    )
    payload = manifest.reconstruction_payload()
    assert payload["page"] == 1
    assert isinstance(payload["blocks"], list)


def test_deterministic_reconstruction(tmp_path: Path):
    pdf = _sample_pdf(tmp_path)
    evidence = PageEvidenceBuilder().build(
        pdf_path=pdf, page_number=1, run_ocr=False
    )
    svc = MarkdownReconstructionService(text_client=None)
    result = svc.reconstruct(evidence)
    assert result.ok
    assert result.markdown
    assert "deterministic_fallback" in ",".join(result.warnings)
