"""Marker consistency checks for figure pipeline."""

from __future__ import annotations

from core.figure_models import FigureRequest, MarkerConsistencyReport
from services.figure_marker_normalizer import FigureMarkerNormalizer, LooseMarkerMatch


class FigureMarkerValidator:
    def __init__(self, *, allow_safe_syntax_repair: bool = True) -> None:
        self.normalizer = FigureMarkerNormalizer()
        self.allow_safe_syntax_repair = allow_safe_syntax_repair

    def validate(
        self,
        *,
        page_number: int,
        markdown: str,
        requests: list[FigureRequest],
    ) -> MarkerConsistencyReport:
        loose = self.normalizer.find_markers(markdown or "")
        md_by_index: dict[int, LooseMarkerMatch] = {m.index: m for m in loose}

        json_indices = sorted(r.figure_index for r in requests)
        md_indices = sorted(md_by_index.keys())

        issues: list[str] = []

        if len(loose) > len(requests):
            issues.append("extra_marker")
        if len(loose) < len(requests):
            issues.append("missing_marker")

        for idx in json_indices:
            if idx not in md_by_index:
                if "missing_marker" not in issues:
                    issues.append("missing_marker")
            elif md_by_index[idx].page != page_number:
                issues.append("figure_marker_mismatch")

        for idx in md_indices:
            if idx not in json_indices:
                if "extra_marker" not in issues:
                    issues.append("extra_marker")
                issues.append("marker_index_conflict")

        blocking = {
            "missing_marker",
            "extra_marker",
            "marker_index_conflict",
            "figure_marker_mismatch",
        }
        needs_review = bool(set(issues) & blocking)

        safe_repairs: list[LooseMarkerMatch] = []
        if (
            self.allow_safe_syntax_repair
            and set(json_indices) == set(md_indices)
            and all(md_by_index[i].page == page_number for i in md_indices)
            and not (set(issues) & blocking)
        ):
            non_strict = [m for m in loose if not m.is_strict]
            if non_strict:
                safe_repairs = non_strict
                if "marker_format" not in issues:
                    issues.append("marker_format")
                needs_review = False

        safe_fix = bool(safe_repairs) and not needs_review

        return MarkerConsistencyReport(
            ok=not needs_review,
            markers_in_md=md_indices,
            figures_in_json=json_indices,
            issues=sorted(set(issues)),
            needs_review=needs_review,
            safe_marker_fix=safe_fix,
            loose_markers=list(loose),
            safe_repairs=safe_repairs,
            marker_repair_type="syntax_only" if safe_fix else None,
        )
