"""Hybrid page transcription: PDF + OCR evidence → text API reconstruction."""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai.providers.provider_factory import create_reconstruction_client
from ai.schemas.transcription import PageTranscriptionResult, VALIDATOR_VERSION
from config.config_manager import load_config
from core.evidence_models import PageEngineMode
from core.models import PageStatus, PipelineStage, StageStatus
from core.project import Project
from services.figure_marker_normalizer import canonical_marker
from services.markdown_reconstruction_service import (
    MarkdownReconstructionService,
    ReconstructionResult,
)
from services.page_evidence_builder import PageEvidenceBuilder
from storage.database import Database
from storage.repository import ProjectRepository
from utils.hashing import file_sha256
from utils.logger import get_logger
from utils.paths import ensure_dir

logger = get_logger("hybrid_transcription")

_LABEL_MARKER_RE = re.compile(
    r"<!--\s*FIGURE\s+page=(\d+)\s+label=([^\s>]+)\s*-->",
    re.IGNORECASE,
)
_INDEX_MARKER_RE = re.compile(
    r"<!--\s*FIGURE\s+page=(\d+)\s+index=(\d+)\s*-->",
    re.IGNORECASE,
)


@dataclass
class HybridPageResult:
    page_number: int
    ok: bool
    markdown: str = ""
    engine: str = PageEngineMode.HYBRID_OCR_API.value
    evidence_path: str = ""
    warnings: list[str] = field(default_factory=list)
    needs_review: bool = False
    error: str | None = None
    reconstruction: ReconstructionResult | None = None
    figures: list[dict[str, Any]] = field(default_factory=list)


class HybridTranscriptionService:
    """Phase 9.5.1 recommended path — not Vision-only."""

    version = "1"

    def __init__(
        self,
        project: Project,
        *,
        text_client: Any | None = None,
        config: dict | None = None,
    ) -> None:
        self.project = project
        self.config = config or load_config()
        self.builder = PageEvidenceBuilder()
        client = text_client if text_client is not None else create_reconstruction_client(
            config=self.config
        )
        self.reconstructor = MarkdownReconstructionService(text_client=client)

    def transcribe_page(
        self,
        page_number: int,
        *,
        run_ocr: bool = True,
        model: str | None = None,
    ) -> HybridPageResult:
        pdf = self.project.info.source_pdf
        pages_dir = self.project.pages_dir
        image = pages_dir / f"page_{page_number:04d}.png"
        if not image.exists():
            alt = pages_dir / f"page_{page_number:04d}.jpg"
            image = alt if alt.exists() else image

        try:
            evidence = self.builder.build(
                pdf_path=pdf,
                page_number=page_number,
                page_image=image if image.exists() else None,
                pdf_hash=self.project.pdf_hash() or file_sha256(pdf),
                run_ocr=run_ocr,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("evidence build failed")
            return HybridPageResult(
                page_number=page_number,
                ok=False,
                error=str(exc),
                warnings=["evidence_build_failed"],
            )

        out_dir = ensure_dir(
            self.project.root
            / "experiments"
            / "hybrid"
            / f"page_{page_number:04d}"
        )
        evidence_path = out_dir / "evidence.json"
        evidence_path.write_text(
            json.dumps(evidence.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        result = self.reconstructor.reconstruct(evidence, model=model)
        md, figures = normalize_hybrid_figures(
            page_number=page_number,
            markdown=result.markdown or "",
            figures=list(result.figures),
            evidence_labels=list(evidence.figure_labels),
        )
        (out_dir / "reconstruction_raw.txt").write_text(
            result.raw or "", encoding="utf-8"
        )
        (out_dir / "reconstruction.md").write_text(md, encoding="utf-8")

        return HybridPageResult(
            page_number=page_number,
            ok=bool(md.strip()) and not result.error,
            markdown=md,
            evidence_path=str(evidence_path),
            warnings=list(evidence.warnings) + list(result.warnings),
            needs_review=result.needs_review or bool(result.uncovered_block_ids),
            error=result.error,
            reconstruction=result,
            figures=figures,
        )

    def accept_canonical(
        self,
        *,
        page_number: int,
        result: HybridPageResult,
        model: str,
        batch_run_id: int | None = None,
        acceptance_mode: str = "auto",
    ) -> Path:
        """Write Vision-compatible canonical md/json so Figure/Assemble can continue."""
        md_body = (result.markdown or "").lstrip()
        if not md_body.strip():
            raise ValueError("hybrid_empty_markdown")

        md, figures = normalize_hybrid_figures(
            page_number=page_number,
            markdown=md_body,
            figures=list(result.figures),
            evidence_labels=[],
        )
        canonical_md = f"<!-- PAGE: {page_number:04d} -->\n\n{md}"

        page_result = PageTranscriptionResult(
            page_number=page_number,
            markdown=md,
            figures=[
                {
                    "figure_index": int(f["figure_index"]),
                    "marker": str(f["marker"]),
                    "figure_type": str(f.get("figure_type") or "figure"),
                    "caption": f.get("caption"),
                    "bbox_1000": f.get("bbox_1000"),
                    "mermaid_candidate": bool(f.get("mermaid_candidate", False)),
                    "needs_review": bool(f.get("needs_review", False)),
                }
                for f in figures
            ],
            warnings=list(result.warnings),
            needs_review=bool(result.needs_review),
        )

        md_dir = ensure_dir(self.project.markdown_pages_dir)
        json_dir = ensure_dir(self.project.page_results_dir)
        md_path = md_dir / f"page_{page_number:04d}.md"
        json_path = json_dir / f"page_{page_number:04d}.json"

        if json_path.exists() or md_path.exists():
            hist = ensure_dir(
                self.project.root
                / "history"
                / "transcription"
                / f"page_{page_number:04d}"
            )
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if md_path.exists():
                (hist / f"{stamp}.md").write_text(
                    md_path.read_text(encoding="utf-8"), encoding="utf-8"
                )
            if json_path.exists():
                (hist / f"{stamp}.json").write_text(
                    json_path.read_text(encoding="utf-8"), encoding="utf-8"
                )

        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "result": page_result.model_dump(),
            "provenance": {
                "provider": "hybrid_ocr_api",
                "model": model,
                "model_digest": "",
                "engine": PageEngineMode.HYBRID_OCR_API.value,
                "evidence_path": result.evidence_path,
                "accepted_at": now,
                "manually_edited": False,
            },
            "acceptance": {
                "mode": acceptance_mode,
                "validator_version": VALIDATOR_VERSION,
                "accepted_at": now,
                "batch_run_id": batch_run_id,
            },
        }
        _atomic_write_text(md_path, canonical_md)
        _atomic_write_text(
            json_path, json.dumps(payload, ensure_ascii=False, indent=2)
        )

        db = Database(self.project.db_path)
        db.initialize()
        repo = ProjectRepository(db)
        try:
            repo.upsert_stage_state(
                page_number,
                PipelineStage.TRANSCRIBE,
                StageStatus.SUCCESS,
                artifact_path=str(json_path),
                settings_hash=f"hybrid:{model}:{page_number}",
                error_message=None,
                finished_at=now,
            )
            repo.update_page_status(
                page_number,
                PageStatus.NEEDS_REVIEW if result.needs_review else PageStatus.SUCCESS,
            )
            conn = db.connect()
            conn.execute(
                """
                UPDATE pages SET markdown_path = ?, json_path = ?, model_name = ?,
                    provider = ?, updated_at = ?
                WHERE page_number = ?
                """,
                (
                    str(md_path),
                    str(json_path),
                    model,
                    "hybrid_ocr_api",
                    now,
                    page_number,
                ),
            )
            conn.commit()
        finally:
            db.close()
        return md_path


def normalize_hybrid_figures(
    *,
    page_number: int,
    markdown: str,
    figures: list[dict[str, Any]],
    evidence_labels: list[str],
) -> tuple[str, list[dict[str, Any]]]:
    """Map label-based Hybrid markers to Vision-compatible index markers."""
    text = markdown or ""
    label_order: list[str] = []

    def _remember(label: str) -> None:
        key = str(label).strip()
        if key and key not in label_order:
            label_order.append(key)

    for m in _LABEL_MARKER_RE.finditer(text):
        _remember(m.group(2))
    for f in figures:
        if not isinstance(f, dict):
            continue
        lab = f.get("label")
        if lab:
            _remember(str(lab))
    for lab in evidence_labels:
        _remember(str(lab))

    # Already-index markers keep their index; fill gaps after
    existing_idx = {
        int(m.group(2)) for m in _INDEX_MARKER_RE.finditer(text)
    }

    label_to_index: dict[str, int] = {}
    next_idx = 1
    for lab in label_order:
        while next_idx in existing_idx:
            next_idx += 1
        label_to_index[lab] = next_idx
        existing_idx.add(next_idx)
        next_idx += 1

    def _repl_label(m: re.Match[str]) -> str:
        lab = m.group(2).strip()
        idx = label_to_index.get(lab) or int(m.group(1))
        return canonical_marker(page_number, idx)

    text = _LABEL_MARKER_RE.sub(_repl_label, text)
    # Force page number consistency on index markers
    text = _INDEX_MARKER_RE.sub(
        lambda m: canonical_marker(page_number, int(m.group(2))),
        text,
    )

    out_figs: list[dict[str, Any]] = []
    seen_idx: set[int] = set()
    for lab, idx in label_to_index.items():
        marker = canonical_marker(page_number, idx)
        if marker not in text:
            text = text.rstrip() + f"\n\n{marker}\n"
        out_figs.append(
            {
                "figure_index": idx,
                "marker": marker,
                "figure_type": "figure",
                "caption": f"Fig. {lab}",
                "bbox_1000": None,
                "mermaid_candidate": False,
                "needs_review": False,
                "label": lab,
            }
        )
        seen_idx.add(idx)

    for m in _INDEX_MARKER_RE.finditer(text):
        idx = int(m.group(2))
        if idx in seen_idx:
            continue
        marker = canonical_marker(page_number, idx)
        out_figs.append(
            {
                "figure_index": idx,
                "marker": marker,
                "figure_type": "figure",
                "caption": f"Fig. {idx}",
                "bbox_1000": None,
                "mermaid_candidate": False,
                "needs_review": False,
            }
        )
        seen_idx.add(idx)

    out_figs.sort(key=lambda x: int(x["figure_index"]))
    return text, out_figs


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        dir=str(path.parent),
        suffix=".tmp",
    ) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)
