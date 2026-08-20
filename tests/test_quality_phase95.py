"""Phase 9.5 quality reconstruction unit tests."""

from __future__ import annotations

from pathlib import Path

from core.figure_models import FigureCandidate, FigureSourceMethod
from core.layout_models import TextSpanStyle
from services.escape_sanitizer import MarkdownEscapeSanitizer
from services.figure_group_service import FigureGroupService, stable_figure_filename
from services.figure_reconciler import FigureReconciler
from services.style_reconstructor import StyleReconstructor
from services.text_coverage_validator import TextCoverageValidator
from storage.database import CURRENT_SCHEMA_VERSION, Database


def test_schema_v10(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    db.initialize()
    assert db.get_schema_version() == CURRENT_SCHEMA_VERSION
    assert CURRENT_SCHEMA_VERSION == 10
    cols = {
        r[1] for r in db.connect().execute("PRAGMA table_info(figures)").fetchall()
    }
    assert "figure_label" in cols
    db.close()


def test_escape_sanitizer_literal_newline():
    s = MarkdownEscapeSanitizer()
    assert s.sanitize("abc\\ndef") == "abc\ndef"
    assert "\\n" not in s.sanitize("line1\\nline2")


def test_escape_sanitizer_preserves_latex():
    s = MarkdownEscapeSanitizer()
    text = r"$\nu \neq \nabla$"
    assert s.sanitize(text) == text
    text2 = "value \\neq 0 and then\\nnext"
    out = s.sanitize(text2)
    assert "\\neq" in out
    assert "\nnext" in out


def test_stable_figure_filename_uses_label_not_index():
    assert stable_figure_filename(4, "2") == "p0004_fig02.png"
    assert stable_figure_filename(4, "3") == "p0004_fig03.png"


def test_style_reconstructor_bold_and_color():
    span = TextSpanStyle(
        text="Important",
        bold=True,
        color_hex="#e60012",
        font="Helvetica-Bold",
    )
    md = StyleReconstructor().span_to_markdown(span)
    assert "**Important**" in md
    assert 'style="color:#e60012"' in md


def test_text_coverage_detects_missing():
    v = TextCoverageValidator(min_pdf_coverage=0.9, min_markdown_coverage=0.5)
    report = v.validate(
        pdf_text="alpha beta gamma delta epsilon zeta",
        markdown="alpha beta",
    )
    assert not report.ok
    assert report.needs_review


def test_figure_group_multi_requires_clip():
    svc = FigureGroupService()
    spans = [
        TextSpanStyle(
            text="Fig. 2. Comparison of methods.",
            bbox_pdf=(50, 500, 400, 520),
            bbox_1000=(50, 700, 400, 750),
        )
    ]
    caps = svc.discover_captions(page_number=4, spans=spans)
    assert len(caps) == 1
    assert caps[0].label == "2"
    cands = [
        FigureCandidate(
            candidate_id="r0",
            page_number=4,
            candidate_type="raster",
            bbox_pdf=(40, 100, 200, 250),
            bbox_1000=(40, 100, 200, 250),
            xref=1,
            source=FigureSourceMethod.PDF_NATIVE,
        ),
        FigureCandidate(
            candidate_id="r1",
            page_number=4,
            candidate_type="raster",
            bbox_pdf=(220, 100, 380, 250),
            bbox_1000=(220, 100, 380, 250),
            xref=2,
            source=FigureSourceMethod.PDF_NATIVE,
        ),
        FigureCandidate(
            candidate_id="v0",
            page_number=4,
            candidate_type="vector",
            bbox_pdf=(40, 260, 200, 400),
            bbox_1000=(40, 260, 200, 400),
            source=FigureSourceMethod.PDF_CLIP,
        ),
        FigureCandidate(
            candidate_id="r2",
            page_number=4,
            candidate_type="raster",
            bbox_pdf=(220, 260, 380, 400),
            bbox_1000=(220, 260, 380, 400),
            xref=3,
            source=FigureSourceMethod.PDF_NATIVE,
        ),
    ]
    groups = svc.build_groups(page_number=4, captions=caps, candidates=cands)
    assert len(groups) == 1
    g = groups[0]
    assert g.figure_label == "2"
    assert g.force_pdf_clip is True
    assert len(g.member_candidate_ids) == 4


def test_reconciler_flags_pdf_missing_in_ai():
    from core.layout_models import FigureGroup, PageLayoutManifest

    manifest = PageLayoutManifest(
        page_number=3,
        figure_groups=[
            FigureGroup(page_number=3, figure_label="1", caption="Fig. 1"),
            FigureGroup(page_number=3, figure_label="2", caption="Fig. 2"),
        ],
    )

    class Fig:
        def __init__(self, caption: str, idx: int) -> None:
            self.caption = caption
            self.figure_index = idx

    report = FigureReconciler().reconcile(
        manifest=manifest, ai_figures=[Fig("Fig. 2 something", 1)]
    )
    assert report.needs_review
    assert "1" in report.missing_in_ai
