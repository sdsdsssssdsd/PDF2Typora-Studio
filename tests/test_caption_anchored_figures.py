"""Caption-anchored figure crop — Fig./图 N drives bbox, no manual review."""

from __future__ import annotations

from core.figure_models import FigureCandidate, FigureRequest
from core.layout_models import CaptionAnchor, FigureGroup
from services.figure_group_service import FigureGroupService, _synthesize_caption_band
from services.figure_marker_normalizer import canonical_marker
from services.figure_service import FigureService


def test_synthesize_caption_band_prefers_above():
    band, b1000 = _synthesize_caption_band(
        caption_bbox_pdf=(50, 400, 400, 420),
        page_width=500,
        page_height=700,
        prev_bottom=100,
        next_top=500,
        prefer_above=True,
    )
    assert band is not None and b1000 is not None
    assert band[3] <= 400  # bottom of crop at/above caption top
    assert band[1] >= 100


def test_build_groups_uses_candidates_above_caption():
    svc = FigureGroupService()
    captions = [
        CaptionAnchor(
            page_number=1,
            label="1",
            raw_text="Fig. 1 Example",
            bbox_pdf=(40, 300, 360, 320),
            bbox_1000=(80, 428, 720, 457),
        )
    ]
    cands = [
        FigureCandidate(
            candidate_id="r1",
            page_number=1,
            candidate_type="raster",
            bbox_pdf=(60, 120, 340, 280),
            bbox_1000=(120, 171, 680, 400),
            xref=1,
        )
    ]
    groups = svc.build_groups(
        page_number=1,
        captions=captions,
        candidates=cands,
        page_width=500,
        page_height=700,
    )
    assert len(groups) == 1
    assert groups[0].figure_label == "1"
    assert groups[0].bbox_1000 is not None
    assert "caption_band_synthesized" not in groups[0].warnings


def test_build_groups_chinese_caption_and_synth():
    svc = FigureGroupService()
    captions = [
        CaptionAnchor(
            page_number=2,
            label="3",
            raw_text="图 3 结果",
            bbox_pdf=(40, 500, 360, 520),
            bbox_1000=(80, 714, 720, 742),
        )
    ]
    groups = svc.build_groups(
        page_number=2,
        captions=captions,
        candidates=[],
        page_width=500,
        page_height=700,
    )
    assert len(groups) == 1
    assert groups[0].bbox_1000 is not None
    assert "caption_band_synthesized" in groups[0].warnings


def test_merge_caption_requests_adds_missing_from_pdf():
    # Minimal stub: call static merge via unbound instance fields
    svc = object.__new__(FigureService)
    from services.figure_reconciler import FigureReconciler

    svc.reconciler = FigureReconciler()
    groups = [
        FigureGroup(
            page_number=1,
            figure_label="2",
            caption="Fig. 2 Demo",
            bbox_1000=(100, 100, 800, 500),
            force_pdf_clip=True,
        )
    ]
    merged = FigureService._merge_caption_requests(
        svc, 1, [], groups, "Some text\n\nFig. 2 Demo\n"
    )
    assert len(merged) == 1
    assert merged[0].figure_index == 2
    assert merged[0].figure_label == "2"
    assert merged[0].group_bbox_1000 == (100, 100, 800, 500)
    assert merged[0].marker == canonical_marker(1, 2)


def test_merge_enriches_existing_ai_request():
    svc = object.__new__(FigureService)
    from services.figure_reconciler import FigureReconciler

    svc.reconciler = FigureReconciler()
    req = FigureRequest(
        page_number=1,
        figure_index=1,
        marker=canonical_marker(1, 1),
        figure_type="image",
        caption="Fig. 1",
        ai_bbox_1000=(10, 10, 20, 20),
    )
    groups = [
        FigureGroup(
            page_number=1,
            figure_label="1",
            caption="Fig. 1 Full",
            bbox_1000=(50, 50, 900, 600),
            force_pdf_clip=True,
            display_index=1,
        )
    ]
    merged = FigureService._merge_caption_requests(svc, 1, [req], groups, "")
    assert len(merged) == 1
    assert merged[0].group_bbox_1000 == (50, 50, 900, 600)
    assert merged[0].figure_label == "1"


def test_ensure_auto_crop_request_fills_bbox():
    req = FigureRequest(
        page_number=1,
        figure_index=1,
        marker=canonical_marker(1, 1),
        figure_type="image",
        caption="Fig. 1 Hello",
        ai_bbox_1000=None,
    )
    out = FigureService._ensure_auto_crop_request(
        req, page_width=500, page_height=700
    )
    assert out.figure_label == "1"
    assert out.group_bbox_1000 is not None
    assert out.force_pdf_clip is True
