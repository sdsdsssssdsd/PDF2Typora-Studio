"""Collect corrected pages for future LoRA/SFT — no training in Phase 9.5."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.paths import ensure_dir


class TrainingDatasetCollector:
    version = "1"

    def __init__(self, project_root: Path) -> None:
        self.root = ensure_dir(project_root / "training_dataset" / project_root.name)

    def page_dir(self, page_number: int) -> Path:
        return ensure_dir(self.root / f"page_{page_number:04d}")

    def record_page(
        self,
        *,
        page_number: int,
        page_png: Path | None = None,
        layout_json: Path | None = None,
        pdf_spans: list[dict[str, Any]] | None = None,
        model_output: dict[str, Any] | None = None,
        corrected_md: str | None = None,
        figures: list[dict[str, Any]] | None = None,
        corrections: dict[str, Any] | None = None,
    ) -> Path:
        d = self.page_dir(page_number)
        if page_png and page_png.exists():
            shutil.copy2(page_png, d / "page.png")
        if layout_json and layout_json.exists():
            shutil.copy2(layout_json, d / "layout.json")
        if pdf_spans is not None:
            (d / "pdf_spans.json").write_text(
                json.dumps(pdf_spans, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        if model_output is not None:
            (d / "model_output.json").write_text(
                json.dumps(model_output, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        if corrected_md is not None:
            (d / "corrected.md").write_text(corrected_md, encoding="utf-8")
        if figures is not None:
            (d / "figures.json").write_text(
                json.dumps(figures, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        payload = corrections or {}
        payload.setdefault(
            "recorded_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        (d / "corrections.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return d
