"""BBox geometry helpers for Figure pipeline."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pymupdf

BBox = tuple[float, float, float, float]
BBox1000 = tuple[int, int, int, int]


def visual_page_size(page: pymupdf.Page) -> tuple[float, float]:
    """Width/height of the page as displayed (matches default get_pixmap)."""
    rot = int(page.rotation or 0) % 360
    r = page.rect
    if rot in (90, 270):
        return float(r.height), float(r.width)
    return float(r.width), float(r.height)


def bbox_1000_to_page_rect(bbox_1000: BBox1000, page: pymupdf.Page) -> pymupdf.Rect:
    """Map normalized visual bbox (AI / rendered page) to PDF clip rect."""
    import pymupdf

    vw, vh = visual_page_size(page)
    x0n, y0n, x1n, y1n = (c / 1000.0 for c in bbox_1000)
    vx0, vy0, vx1, vy1 = x0n * vw, y0n * vh, x1n * vw, y1n * vh
    r = page.rect
    rot = int(page.rotation or 0) % 360
    if rot == 0:
        rect = pymupdf.Rect(vx0, vy0, vx1, vy1)
    elif rot == 90:
        rect = pymupdf.Rect(vy0, r.height - vx1, vy1, r.height - vx0)
    elif rot == 180:
        rect = pymupdf.Rect(r.width - vx1, r.height - vy1, r.width - vx0, r.height - vy0)
    elif rot == 270:
        rect = pymupdf.Rect(r.width - vy1, vx0, r.width - vy0, vx1)
    else:
        rect = pymupdf.Rect(vx0, vy0, vx1, vy1)
    return clamp_rect(rect, r)


def page_rect_to_bbox_1000(rect: pymupdf.Rect, page: pymupdf.Page) -> BBox1000:
    """Inverse of bbox_1000_to_page_rect (approximate, for candidates)."""
    vw, vh = visual_page_size(page)
    r = page.rect
    rot = int(page.rotation or 0) % 360
    if rot == 0:
        vx0, vy0, vx1, vy1 = rect.x0, rect.y0, rect.x1, rect.y1
    elif rot == 90:
        vx0 = r.height - rect.y1
        vy0 = rect.x0
        vx1 = r.height - rect.y0
        vy1 = rect.x1
    elif rot == 180:
        vx0 = r.width - rect.x1
        vy0 = r.height - rect.y1
        vx1 = r.width - rect.x0
        vy1 = r.height - rect.y0
    elif rot == 270:
        vx0 = rect.y0
        vy0 = r.width - rect.x1
        vx1 = rect.y1
        vy1 = r.width - rect.x0
    else:
        vx0, vy0, vx1, vy1 = rect.x0, rect.y0, rect.x1, rect.y1

    def _n(v: float, denom: float) -> int:
        return max(0, min(1000, int(round(v / denom * 1000))))

    return (_n(vx0, vw), _n(vy0, vh), _n(vx1, vw), _n(vy1, vh))


def intersection(a: BBox1000, b: BBox1000) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    return float((ix1 - ix0) * (iy1 - iy0))


def area(b: BBox1000) -> float:
    x0, y0, x1, y1 = b
    return max(0.0, float(x1 - x0) * float(y1 - y0))


def iou(a: BBox1000, b: BBox1000) -> float:
    inter = intersection(a, b)
    if inter <= 0:
        return 0.0
    union = area(a) + area(b) - inter
    return inter / union if union > 0 else 0.0


def containment_ratio(inner: BBox1000, outer: BBox1000) -> float:
    inter = intersection(inner, outer)
    inn = area(inner)
    return inter / inn if inn > 0 else 0.0


def center_distance_normalized(a: BBox1000, b: BBox1000) -> float:
    def center(bb: BBox1000) -> tuple[float, float]:
        return ((bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0)

    ca, cb = center(a), center(b)
    dist = math.hypot(ca[0] - cb[0], ca[1] - cb[1])
    return dist / 1000.0


def expand_rect(rect: "pymupdf.Rect", pad_x: float, pad_y: float) -> "pymupdf.Rect":
    import pymupdf

    return pymupdf.Rect(
        rect.x0 - pad_x,
        rect.y0 - pad_y,
        rect.x1 + pad_x,
        rect.y1 + pad_y,
    )


def clamp_rect(rect: "pymupdf.Rect", bounds: "pymupdf.Rect") -> "pymupdf.Rect":
    import pymupdf

    return pymupdf.Rect(
        max(bounds.x0, min(rect.x0, bounds.x1)),
        max(bounds.y0, min(rect.y0, bounds.y1)),
        max(bounds.x0, min(rect.x1, bounds.x1)),
        max(bounds.y0, min(rect.y1, bounds.y1)),
    )


def expand_bbox_1000(
    bbox: BBox1000, padding_ratio: float
) -> BBox1000:
    x0, y0, x1, y1 = bbox
    pw = int(round(1000 * padding_ratio))
    ph = int(round(1000 * padding_ratio))
    return (
        max(0, x0 - pw),
        max(0, y0 - ph),
        min(1000, x1 + pw),
        min(1000, y1 + ph),
    )
