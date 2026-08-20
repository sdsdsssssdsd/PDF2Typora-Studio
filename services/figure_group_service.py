"""Discover caption anchors and build FigureGroup units (not subplots)."""

from __future__ import annotations

import re
from typing import Any

from core.figure_models import FigureCandidate
from core.layout_models import CaptionAnchor, FigureGroup, TextSpanStyle
from utils.logger import get_logger

logger = get_logger("figure_group")

_CAPTION_RE = re.compile(
    r"(?i)(?:\b(?:fig(?:ure)?|abb(?:ildung)?)\.?\s*|图\s*)"
    r"([0-9]+[a-z]?)\s*[.:：\-]?\s*(.*)$"
)
_SUBFIG_RE = re.compile(r"\(\s*([a-z])\s\)", re.I)


def stable_figure_filename(page_number: int, figure_label: str, ext: str = ".png") -> str:
    """Identity is Fig label, not AI figure_index."""
    label = re.sub(r"[^0-9A-Za-z]+", "", figure_label) or "0"
    if label.isdigit():
        return f"p{page_number:04d}_fig{int(label):02d}{ext}"
    return f"p{page_number:04d}_fig{label}{ext}"


def union_bbox_pdf(
    boxes: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float] | None:
    if not boxes:
        return None
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    return (x0, y0, x1, y1)


def union_bbox_1000(
    boxes: list[tuple[int, int, int, int]],
) -> tuple[int, int, int, int] | None:
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _synthesize_caption_band(
    *,
    caption_bbox_pdf: tuple[float, float, float, float],
    page_width: float,
    page_height: float,
    prev_bottom: float | None,
    next_top: float | None,
    prefer_above: bool = True,
) -> tuple[tuple[float, float, float, float] | None, tuple[int, int, int, int] | None]:
    """When no image candidates, crop a band above (or below) the caption."""
    x0, y0, x1, y1 = caption_bbox_pdf
    margin_x = max(8.0, page_width * 0.04)
    left = max(0.0, min(x0, margin_x) - 4)
    right = min(page_width, max(x1, page_width - margin_x) + 4)
    if prefer_above:
        bottom = max(0.0, y0 - 2)
        top = prev_bottom if prev_bottom is not None else max(0.0, bottom - page_height * 0.35)
        top = max(0.0, min(top, bottom - 24))
        if bottom - top < 24:
            top = max(0.0, bottom - min(180.0, page_height * 0.25))
        band = (left, top, right, bottom)
    else:
        top = min(page_height, y1 + 2)
        bottom = next_top if next_top is not None else min(page_height, top + page_height * 0.35)
        bottom = min(page_height, max(bottom, top + 24))
        band = (left, top, right, bottom)
    if page_width <= 0 or page_height <= 0:
        return band, None
    b1000 = (
        int(max(0, min(1000, band[0] / page_width * 1000))),
        int(max(0, min(1000, band[1] / page_height * 1000))),
        int(max(0, min(1000, band[2] / page_width * 1000))),
        int(max(0, min(1000, band[3] / page_height * 1000))),
    )
    if b1000[2] <= b1000[0] or b1000[3] <= b1000[1]:
        return band, None
    return band, b1000


class FigureGroupService:
    version = "1"

    def discover_captions(
        self,
        *,
        page_number: int,
        spans: list[TextSpanStyle],
        plain_text: str = "",
    ) -> list[CaptionAnchor]:
        anchors: list[CaptionAnchor] = []
        seen: set[str] = set()
        # Prefer span-level (has bbox)
        for span in spans:
            m = _CAPTION_RE.match(span.text.strip())
            if not m:
                # caption may be split — try startswith Fig
                if not re.match(
                    r"(?i)^(?:fig(?:ure)?|abb)\.?\s*\d", span.text.strip()
                ) and not re.match(r"^图\s*\d", span.text.strip()):
                    continue
                m = _CAPTION_RE.search(span.text.strip())
            if not m:
                continue
            label = m.group(1)
            key = label.lower()
            if key in seen:
                continue
            seen.add(key)
            anchors.append(
                CaptionAnchor(
                    page_number=page_number,
                    label=label,
                    raw_text=span.text.strip(),
                    kind="figure",
                    bbox_pdf=span.bbox_pdf,
                    bbox_1000=span.bbox_1000,
                )
            )
        # Fallback: plain text lines
        if not anchors and plain_text:
            for line in plain_text.splitlines():
                m = _CAPTION_RE.search(line.strip())
                if not m:
                    continue
                label = m.group(1)
                key = label.lower()
                if key in seen:
                    continue
                seen.add(key)
                anchors.append(
                    CaptionAnchor(
                        page_number=page_number,
                        label=label,
                        raw_text=line.strip(),
                        kind="figure",
                    )
                )
        return anchors

    def build_groups(
        self,
        *,
        page_number: int,
        captions: list[CaptionAnchor],
        candidates: list[FigureCandidate],
        ai_requests: list[Any] | None = None,
        page_width: float = 0.0,
        page_height: float = 0.0,
    ) -> list[FigureGroup]:
        """Group visual candidates under each Fig. N / 图 N caption by spatial proximity."""
        groups: list[FigureGroup] = []
        used: set[str] = set()

        # Sort captions top-to-bottom
        caps = sorted(
            captions,
            key=lambda c: (c.bbox_pdf[1] if c.bbox_pdf else 1e9, c.label),
        )

        if page_width <= 0 or page_height <= 0:
            for c in candidates:
                if c.bbox_pdf:
                    page_width = max(page_width, c.bbox_pdf[2])
                    page_height = max(page_height, c.bbox_pdf[3])
            for cap in caps:
                if cap.bbox_pdf:
                    page_width = max(page_width, cap.bbox_pdf[2])
                    page_height = max(page_height, cap.bbox_pdf[3])
            page_width = page_width or 595.0
            page_height = page_height or 842.0

        for i, cap in enumerate(caps):
            next_y = (
                caps[i + 1].bbox_pdf[1]
                if i + 1 < len(caps) and caps[i + 1].bbox_pdf
                else None
            )
            prev_y = (
                caps[i - 1].bbox_pdf[3]
                if i > 0 and caps[i - 1].bbox_pdf
                else None
            )
            members_above: list[FigureCandidate] = []
            members_below: list[FigureCandidate] = []
            for cand in candidates:
                if cand.candidate_id in used or not cand.bbox_pdf:
                    continue
                cy = (cand.bbox_pdf[1] + cand.bbox_pdf[3]) / 2
                if not cap.bbox_pdf:
                    members_above.append(cand)
                    continue
                cap_y0, cap_y1 = cap.bbox_pdf[1], cap.bbox_pdf[3]
                # Prefer: visual content above the caption line
                if cy <= cap_y0 + 5:
                    if prev_y is not None and cy < prev_y - 2:
                        continue
                    members_above.append(cand)
                # Fallback zone: immediately below caption until next caption
                elif cy >= cap_y1 - 2:
                    if next_y is not None and cy >= next_y:
                        continue
                    members_below.append(cand)

            members = members_above or members_below
            placement = "above" if members_above else ("below" if members_below else "none")

            # If none matched by geometry, synthesize a crop band near the caption
            pdf_boxes = [m.bbox_pdf for m in members if m.bbox_pdf]
            b1000 = [m.bbox_1000 for m in members if m.bbox_1000]
            synth_pdf = None
            synth_1000 = None
            if not members and cap.bbox_pdf:
                synth_pdf, synth_1000 = _synthesize_caption_band(
                    caption_bbox_pdf=cap.bbox_pdf,
                    page_width=page_width,
                    page_height=page_height,
                    prev_bottom=prev_y,
                    next_top=next_y,
                    prefer_above=True,
                )
                # If above band is tiny, try below
                if synth_1000 is None or (
                    synth_pdf and abs(synth_pdf[3] - synth_pdf[1]) < 30
                ):
                    synth_pdf, synth_1000 = _synthesize_caption_band(
                        caption_bbox_pdf=cap.bbox_pdf,
                        page_width=page_width,
                        page_height=page_height,
                        prev_bottom=prev_y,
                        next_top=next_y,
                        prefer_above=False,
                    )
                pdf_boxes = [synth_pdf] if synth_pdf else []
                b1000 = [synth_1000] if synth_1000 else []

            for m in members:
                used.add(m.candidate_id)

            subfigs = sorted(
                {
                    s.lower()
                    for m in members
                    for s in _SUBFIG_RE.findall(
                        str((m.metadata or {}).get("label") or "")
                    )
                }
            )
            force_clip = True
            if len(members) == 1 and members[0].candidate_type == "raster":
                force_clip = bool(members[0].metadata.get("has_mask")) or False
                force_clip = False if members[0].xref else True
            if len(members) > 1:
                force_clip = True
            if any(m.candidate_type == "vector" for m in members):
                force_clip = True
            if not members:
                force_clip = True

            warnings: list[str] = []
            if placement == "below":
                warnings.append("caption_figure_below")
            if not members and synth_1000:
                warnings.append("caption_band_synthesized")
            if len(members) > 1:
                warnings.append("multi_member_requires_pdf_clip")

            g = FigureGroup(
                page_number=page_number,
                figure_label=cap.label,
                caption=cap.raw_text,
                caption_anchor=cap,
                subfigures=subfigs,
                member_candidate_ids=[m.candidate_id for m in members],
                bbox_pdf=union_bbox_pdf(pdf_boxes) if pdf_boxes else synth_pdf,  # type: ignore[arg-type]
                bbox_1000=union_bbox_1000(b1000) if b1000 else synth_1000,  # type: ignore[arg-type]
                force_pdf_clip=force_clip or len(members) != 1 or not members,
                warnings=warnings,
            )
            if len(members) > 1:
                g.force_pdf_clip = True
            g.ensure_id()
            groups.append(g)

        # Unreferenced visual candidates
        orphan_ids = [
            c.candidate_id
            for c in candidates
            if c.candidate_id not in used
            and not (c.metadata or {}).get("low_priority")
            and (c.metadata or {}).get("special") != "full_page_image"
            and (c.bbox_pdf is not None)
            # Without caption anchors, only elevate raster (not every vector scribble)
            and (c.candidate_type == "raster" or bool(groups))
        ]
        if orphan_ids and not groups:
            # No captions — do NOT invent Fig.1..N for every vector cluster.
            # Record a single review placeholder listing orphans.
            g = FigureGroup(
                page_number=page_number,
                figure_label="?",
                member_candidate_ids=list(orphan_ids),
                bbox_pdf=union_bbox_pdf(
                    [c.bbox_pdf for c in candidates if c.candidate_id in orphan_ids and c.bbox_pdf]  # type: ignore[misc]
                ),
                force_pdf_clip=True,
                warnings=[
                    "UNREFERENCED_FIGURE_CANDIDATE",
                    "no_caption_anchor",
                    f"orphan_ids:{','.join(orphan_ids)}",
                ],
                display_index=None,
            )
            g.ensure_id()
            groups.append(g)
        elif orphan_ids:
            for g in groups:
                g.warnings.append(
                    f"UNREFERENCED_FIGURE_CANDIDATE:{','.join(orphan_ids)}"
                )
                break

        # Bind display_index from AI requests by caption label when possible
        if ai_requests:
            for g in groups:
                for req in ai_requests:
                    cap = (getattr(req, "caption", None) or "") + " " + (
                        getattr(req, "marker", None) or ""
                    )
                    if re.search(
                        rf"(?i)(?:fig(?:ure)?\.?\s*|图\s*){re.escape(g.figure_label)}\b",
                        cap,
                    ):
                        g.display_index = getattr(req, "figure_index", None)
                        break

        return groups
