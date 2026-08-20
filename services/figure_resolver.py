"""Replace FIGURE markers in canonical markdown with image links."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.figure_models import FIGURE_PIPELINE_VERSION
from services.transcription_validator import FIGURE_MARKER_RE
from utils.hashing import file_sha256, resolved_page_hash, text_sha256
from utils.logger import get_logger
from utils.paths import ensure_dir

logger = get_logger("figure_resolver")

RESOLVER_VERSION = "1"


def figure_filename(page_number: int, figure_index: int, ext: str = ".png") -> str:
    return f"p{page_number:04d}_fig{figure_index:02d}{ext}"


def marker_to_image_md(page_number: int, figure_index: int, ext: str = ".png") -> str:
    rel = f"figures/{figure_filename(page_number, figure_index, ext)}"
    return f"![图]({rel})"


class FigureResolver:
    def __init__(self, resolved_dir: Path) -> None:
        self.resolved_dir = ensure_dir(resolved_dir)

    def resolve_page(
        self,
        *,
        page_number: int,
        canonical_md: str,
        figure_paths: dict[int, str],
        figure_hashes: dict[int, str],
        skip_indices: set[int] | None = None,
    ) -> tuple[str, str]:
        """Return (resolved_md, resolved_hash). Does not touch canonical."""
        skip = skip_indices or set()
        text = canonical_md

        def repl(match: re.Match[str]) -> str:
            pg = int(match.group(1))
            idx = int(match.group(2))
            if pg != page_number or idx in skip:
                return match.group(0)
            path = figure_paths.get(idx)
            if not path:
                return match.group(0)
            ext = Path(path).suffix or ".png"
            return marker_to_image_md(page_number, idx, ext)

        resolved = FIGURE_MARKER_RE.sub(repl, text)
        canon_hash = text_sha256(canonical_md)
        r_hash = resolved_page_hash(
            canonical_md_hash=canon_hash,
            figure_hashes=[figure_hashes[i] for i in sorted(figure_paths)],
            resolver_version=RESOLVER_VERSION,
        )
        return resolved, r_hash

    def write_resolved(
        self, page_number: int, content: str, *, force: bool = False
    ) -> Path:
        path = self.resolved_dir / f"page_{page_number:04d}.md"
        if path.exists() and not force:
            old_hash = file_sha256(path)
            new_hash = text_sha256(content)
            if old_hash == text_sha256(path.read_text(encoding="utf-8")):
                return path
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
        return path

    def copy_canonical(self, page_number: int, canonical_md: str) -> Path:
        return self.write_resolved(page_number, canonical_md, force=True)
