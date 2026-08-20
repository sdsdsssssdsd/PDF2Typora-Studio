"""Bidirectional figure reconciliation: AI ↔ PDF layout."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.layout_models import FigureGroup, PageLayoutManifest


@dataclass
class FigureReconcileReport:
    ok: bool
    needs_review: bool = False
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ai_labels: list[str] = field(default_factory=list)
    pdf_labels: list[str] = field(default_factory=list)
    unreferenced_candidates: list[str] = field(default_factory=list)
    missing_in_ai: list[str] = field(default_factory=list)
    missing_in_pdf: list[str] = field(default_factory=list)


class FigureReconciler:
    """AI has figure but PDF doesn't → error; PDF has figure but AI doesn't → error."""

    version = "2"

    def reconcile(
        self,
        *,
        manifest: PageLayoutManifest,
        ai_figures: list[Any],
    ) -> FigureReconcileReport:
        pdf_labels = [g.figure_label for g in manifest.figure_groups]
        ai_labels: list[str] = []
        for fig in ai_figures:
            cap = getattr(fig, "caption", None) or ""
            if isinstance(fig, dict):
                cap = fig.get("caption") or ""
                idx = fig.get("figure_index")
            else:
                idx = getattr(fig, "figure_index", None)
            m = re.search(r"(?i)(?:fig(?:ure)?|abb)\.?\s*([0-9]+[a-z]?)", cap)
            if m:
                ai_labels.append(m.group(1))
            elif idx is not None:
                ai_labels.append(f"index:{idx}")

        pdf_set = {x.lower() for x in pdf_labels}
        ai_set = {x.lower() for x in ai_labels if not x.startswith("index:")}

        missing_in_ai = sorted(pdf_set - ai_set)
        missing_in_pdf = sorted(ai_set - pdf_set)

        unreferenced: list[str] = []
        for g in manifest.figure_groups:
            for w in g.warnings:
                if w.startswith("UNREFERENCED_FIGURE_CANDIDATE"):
                    unreferenced.append(w)

        # Also: image candidates with no group membership
        grouped = {
            cid
            for g in manifest.figure_groups
            for cid in g.member_candidate_ids
        }
        for img in manifest.image_candidates:
            cid = img.get("candidate_id")
            meta = img.get("metadata") or {}
            if cid and cid not in grouped and not meta.get("low_priority"):
                if meta.get("special") != "full_page_image":
                    unreferenced.append(f"UNREFERENCED_FIGURE_CANDIDATE:{cid}")

        issues: list[str] = []
        warnings: list[str] = []
        if missing_in_ai:
            issues.append(f"PDF_FIGURE_MISSING_IN_AI:{','.join(missing_in_ai)}")
        if missing_in_pdf:
            issues.append(f"AI_FIGURE_MISSING_IN_PDF:{','.join(missing_in_pdf)}")
        if unreferenced:
            issues.append("UNREFERENCED_FIGURE_CANDIDATE")
            warnings.extend(unreferenced)

        needs_review = bool(issues)
        return FigureReconcileReport(
            ok=not needs_review,
            needs_review=needs_review,
            issues=issues,
            warnings=warnings,
            ai_labels=ai_labels,
            pdf_labels=pdf_labels,
            unreferenced_candidates=unreferenced,
            missing_in_ai=missing_in_ai,
            missing_in_pdf=missing_in_pdf,
        )

    def prefer_group_for_request(
        self,
        *,
        groups: list[FigureGroup],
        figure_index: int,
        caption: str | None,
    ) -> FigureGroup | None:
        """Map AI request to FigureGroup by caption label — never by index alone."""
        if caption:
            m = re.search(
                r"(?i)(?:fig(?:ure)?|abb)\.?\s*([0-9]+[a-z]?)|图\s*([0-9]+[a-z]?)",
                caption,
            )
            if m:
                label = (m.group(1) or m.group(2) or "").lower()
                for g in groups:
                    if g.figure_label.lower() == label:
                        return g
        for g in groups:
            if g.display_index == figure_index:
                return g
        # Fallback: numeric label equals request index
        for g in groups:
            try:
                if int(re.sub(r"[^0-9]", "", g.figure_label) or "0") == figure_index:
                    return g
            except ValueError:
                continue
        return None
