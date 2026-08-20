"""Match AI figure requests to PDF candidates."""

from __future__ import annotations

from typing import Any

from core.figure_models import FigureCandidate, FigureMatch, FigureRequest
from utils.geometry import (
    area,
    center_distance_normalized,
    containment_ratio,
    iou,
)


class FigureMatcher:
    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        cfg = cfg or {}
        matching = cfg.get("matching") or {}
        self.auto_threshold = float(matching.get("auto_threshold", 0.85))
        self.review_threshold = float(matching.get("review_threshold", 0.55))

    def match(
        self,
        request: FigureRequest,
        candidates: list[FigureCandidate],
        *,
        marker_ok: bool = True,
    ) -> FigureMatch:
        ai = request.ai_bbox_1000
        raster = [c for c in candidates if c.candidate_type == "raster"]
        vector = [c for c in candidates if c.candidate_type == "vector"]

        if not marker_ok:
            return FigureMatch(
                request=request,
                candidate=None,
                score=0.0,
                strategy="marker_mismatch",
                auto_resolvable=False,
                reasons=["marker_mismatch"],
            )

        if ai is None:
            return self._unambiguous_without_bbox(request, raster)

        scored: list[tuple[float, FigureCandidate, dict[str, float]]] = []
        for cand in raster + vector:
            if cand.bbox_1000 is None:
                continue
            metrics = {
                "iou": iou(ai, cand.bbox_1000),
                "containment": max(
                    containment_ratio(ai, cand.bbox_1000),
                    containment_ratio(cand.bbox_1000, ai),
                ),
                "center_dist": center_distance_normalized(ai, cand.bbox_1000),
            }
            score = self._score(metrics, cand.candidate_type)
            scored.append((score, cand, metrics))

        if not scored:
            return FigureMatch(
                request=request,
                candidate=None,
                score=0.0,
                strategy="no_candidate",
                auto_resolvable=False,
                reasons=["no_candidate"],
            )

        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best, metrics = scored[0]
        second = scored[1][0] if len(scored) > 1 else 0.0

        reasons: list[str] = []
        auto = False
        strategy = "ai_bbox_match"

        if len(scored) > 1 and second >= self.review_threshold:
            if abs(best_score - second) < 0.08:
                reasons.append("ambiguous_candidate")
                auto = False
        elif best_score >= self.auto_threshold:
            auto = True
        elif best_score >= self.review_threshold:
            reasons.append("low_match_score")
        else:
            reasons.append("low_match_score")
            strategy = "ai_bbox_clip_fallback"

        return FigureMatch(
            request=request,
            candidate=best,
            iou=metrics["iou"],
            containment=metrics["containment"],
            center_distance=metrics["center_dist"],
            score=best_score,
            strategy=strategy,
            auto_resolvable=auto and "ambiguous_candidate" not in reasons,
            reasons=reasons,
        )

    def _unambiguous_without_bbox(
        self, request: FigureRequest, raster: list[FigureCandidate]
    ) -> FigureMatch:
        usable = [c for c in raster if not c.metadata.get("low_priority")]
        if len(usable) == 1:
            c = usable[0]
            return FigureMatch(
                request=request,
                candidate=c,
                score=0.75,
                strategy="single_native_image",
                auto_resolvable=True,
                reasons=[],
            )
        return FigureMatch(
            request=request,
            candidate=None,
            score=0.0,
            strategy="no_bbox",
            auto_resolvable=False,
            reasons=["invalid_ai_bbox", "no_candidate"],
        )

    @staticmethod
    def _score(metrics: dict[str, float], candidate_type: str) -> float:
        base = (
            0.45 * metrics["iou"]
            + 0.35 * metrics["containment"]
            + 0.20 * max(0.0, 1.0 - metrics["center_dist"] * 2.0)
        )
        if candidate_type == "vector":
            base *= 0.92
        return min(1.0, max(0.0, base))
