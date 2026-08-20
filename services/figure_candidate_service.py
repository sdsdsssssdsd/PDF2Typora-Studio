"""Discover PDF raster and vector figure candidates."""

from __future__ import annotations

from typing import Any

import pymupdf

from core.figure_models import FigureCandidate, FigureSourceMethod
from utils.geometry import page_rect_to_bbox_1000
from utils.logger import get_logger

logger = get_logger("figure_candidate")


class FigureCandidateService:
    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        cfg = cfg or {}
        native = cfg.get("native_candidate") or {}
        self.min_w = int(native.get("min_width_px", 40))
        self.min_h = int(native.get("min_height_px", 40))
        self.detect_vector = bool(cfg.get("detect_vector", True))

    def discover(
        self, page: pymupdf.Page, page_number: int
    ) -> list[FigureCandidate]:
        out: list[FigureCandidate] = []
        out.extend(self._raster_candidates(page, page_number))
        if self.detect_vector:
            out.extend(self._vector_clusters(page, page_number))
        return out

    def _raster_candidates(
        self, page: pymupdf.Page, page_number: int
    ) -> list[FigureCandidate]:
        candidates: list[FigureCandidate] = []
        try:
            infos = page.get_image_info(hashes=True, xrefs=True)
        except TypeError:
            infos = page.get_image_info()
        vw, vh = page.rect.width, page.rect.height
        page_area = max(vw * vh, 1.0)
        for i, info in enumerate(infos or []):
            bbox = info.get("bbox")
            if not bbox or len(bbox) < 4:
                continue
            rect = pymupdf.Rect(bbox)
            w = int(info.get("width") or rect.width)
            h = int(info.get("height") or rect.height)
            coverage = (rect.width * rect.height) / page_area
            meta: dict[str, Any] = {
                "has_mask": bool(info.get("has-mask") or info.get("has_mask")),
                "transform": info.get("transform"),
                "coverage": coverage,
            }
            if coverage > 0.95:
                meta["special"] = "full_page_image"
            low_priority = w < self.min_w or h < self.min_h
            if low_priority:
                meta["low_priority"] = True
            b1000 = page_rect_to_bbox_1000(rect, page)
            candidates.append(
                FigureCandidate(
                    candidate_id=f"r{i}",
                    page_number=page_number,
                    candidate_type="raster",
                    bbox_pdf=(rect.x0, rect.y0, rect.x1, rect.y1),
                    bbox_1000=b1000,
                    xref=int(info["xref"]) if info.get("xref") else None,
                    digest=str(info.get("digest") or ""),
                    width=w,
                    height=h,
                    source=FigureSourceMethod.PDF_NATIVE,
                    metadata=meta,
                )
            )
        return candidates

    def _vector_clusters(
        self, page: pymupdf.Page, page_number: int
    ) -> list[FigureCandidate]:
        try:
            drawings = page.get_cdrawings()
        except Exception:  # noqa: BLE001
            try:
                drawings = page.get_drawings()
            except Exception:  # noqa: BLE001
                return []

        rects: list[pymupdf.Rect] = []
        for d in drawings or []:
            r = d.get("rect")
            if r is None:
                continue
            rect = pymupdf.Rect(r)
            if rect.width < 2 or rect.height < 2:
                continue
            rects.append(rect)

        if not rects:
            return []

        clusters = self._cluster_rects(rects, gap=12.0)
        out: list[FigureCandidate] = []
        for i, cluster in enumerate(clusters):
            if cluster.is_empty:
                continue
            b1000 = page_rect_to_bbox_1000(cluster, page)
            out.append(
                FigureCandidate(
                    candidate_id=f"v{i}",
                    page_number=page_number,
                    candidate_type="vector",
                    bbox_pdf=(cluster.x0, cluster.y0, cluster.x1, cluster.y1),
                    bbox_1000=b1000,
                    source=FigureSourceMethod.PDF_CLIP,
                    metadata={"path_count": len(rects)},
                )
            )
        return out

    @staticmethod
    def _cluster_rects(rects: list[pymupdf.Rect], gap: float) -> list[pymupdf.Rect]:
        if not rects:
            return []
        groups: list[pymupdf.Rect] = []
        for rect in rects:
            merged = False
            for idx, g in enumerate(groups):
                expanded = pymupdf.Rect(
                    g.x0 - gap, g.y0 - gap, g.x1 + gap, g.y1 + gap
                )
                if expanded.intersects(rect):
                    groups[idx] = g | rect
                    merged = True
                    break
            if not merged:
                groups.append(pymupdf.Rect(rect))
        # second pass merge
        changed = True
        while changed and len(groups) > 1:
            changed = False
            next_groups: list[pymupdf.Rect] = []
            for g in groups:
                merged = False
                for idx, h in enumerate(next_groups):
                    expanded = pymupdf.Rect(
                        h.x0 - gap, h.y0 - gap, h.x1 + gap, h.y1 + gap
                    )
                    if expanded.intersects(g):
                        next_groups[idx] = h | g
                        merged = True
                        changed = True
                        break
                if not merged:
                    next_groups.append(g)
            groups = next_groups
        return groups
