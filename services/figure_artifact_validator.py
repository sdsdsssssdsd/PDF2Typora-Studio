"""Validate extracted figure artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.figure_models import FigureArtifactResult


class FigureArtifactValidator:
    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        cfg = cfg or {}
        validation = cfg.get("validation") or {}
        self.min_w = int(validation.get("minimum_width_px", 40))
        self.min_h = int(validation.get("minimum_height_px", 40))

    def validate(
        self,
        artifact: FigureArtifactResult,
        *,
        page_width: float,
        page_height: float,
    ) -> FigureArtifactResult:
        if not artifact.valid or not artifact.artifact_path:
            return artifact
        path = Path(artifact.artifact_path)
        if not path.exists() or path.stat().st_size == 0:
            artifact.valid = False
            artifact.errors.append("artifact_invalid")
            return artifact

        w, h = artifact.width, artifact.height
        if w is None or h is None:
            artifact.valid = False
            artifact.errors.append("artifact_invalid")
            return artifact

        if w < self.min_w or h < self.min_h:
            artifact.valid = False
            artifact.errors.append("crop_too_small")
            return artifact

        page_area = max(page_width * page_height, 1.0)
        if (w * h) / page_area > 0.98:
            artifact.warnings.append("crop_nearly_full_page")

        return artifact
