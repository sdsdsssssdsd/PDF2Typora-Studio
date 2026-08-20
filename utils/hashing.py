"""Hashing utilities."""

from __future__ import annotations

import hashlib
from pathlib import Path

from core.models import RENDER_PIPELINE_VERSION, RenderSettings, TranscriptionOptions


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    # 1 MiB chunks — fewer syscalls on large PDFs during import/render cache
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_settings_hash(
    pdf_hash: str,
    page_number: int,
    settings: RenderSettings,
) -> str:
    """Stable hash for render cache keys (does not re-hash the PDF file)."""
    payload = "|".join(
        [
            pdf_hash,
            str(page_number),
            str(settings.dpi),
            settings.image_format.lower(),
            settings.colorspace.lower(),
            "1" if settings.alpha else "0",
            RENDER_PIPELINE_VERSION,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def transcription_request_hash(
    *,
    image_hash: str,
    page_number: int,
    model_name: str,
    model_digest: str,
    prompt_hash: str,
    schema_version: str,
    options: TranscriptionOptions,
    pipeline_version: str,
) -> str:
    payload = "|".join(
        [
            image_hash,
            str(page_number),
            model_name,
            model_digest or "",
            prompt_hash,
            schema_version,
            str(options.temperature),
            str(options.num_ctx if options.num_ctx is not None else "auto"),
            str(options.think),
            pipeline_version,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def figure_artifact_hash(
    *,
    pdf_hash: str,
    page_number: int,
    figure_index: int,
    source_method: str,
    xref: int | None,
    digest: str | None,
    crop_bbox: tuple[float, float, float, float] | None,
    crop_dpi: int,
    padding_ratio: float,
    pipeline_version: str,
) -> str:
    bbox_s = ",".join(f"{v:.3f}" for v in crop_bbox) if crop_bbox else ""
    payload = "|".join(
        [
            pdf_hash,
            str(page_number),
            str(figure_index),
            source_method,
            str(xref or ""),
            digest or "",
            bbox_s,
            str(crop_dpi),
            f"{padding_ratio:.6f}",
            pipeline_version,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolved_page_hash(
    *,
    canonical_md_hash: str,
    figure_hashes: list[str],
    resolver_version: str,
    marker_repair_digest: str = "",
    placement_digest: str = "",
    skip_digest: str = "",
) -> str:
    payload = "|".join(
        [
            canonical_md_hash,
            marker_repair_digest,
            placement_digest,
            skip_digest,
            *sorted(figure_hashes),
            resolver_version,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
