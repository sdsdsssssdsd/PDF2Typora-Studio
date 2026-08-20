"""Build resolved markdown from canonical + repairs + accepted figures."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.figure_marker_normalizer import FigureMarkerNormalizer, canonical_marker
from services.figure_resolver import FigureResolver, marker_to_image_md
from services.transcription_validator import FIGURE_MARKER_RE
from utils.hashing import resolved_page_hash, text_sha256
from utils.logger import get_logger
from utils.paths import ensure_dir

logger = get_logger("resolved_page_builder")

RESOLVER_VERSION = "2"


@dataclass
class MarkerRepairRecord:
    figure_index: int
    original: str
    normalized: str
    repair_type: str = "syntax_only"


@dataclass
class ManualMarkerPlacement:
    figure_index: int
    page_number: int
    char_offset: int
    before_context: str = ""
    after_context: str = ""
    manually_inserted_marker: bool = True


@dataclass
class ResolvedPageInput:
    page_number: int
    canonical_md: str
    marker_repairs: list[MarkerRepairRecord] = field(default_factory=list)
    manual_placements: list[ManualMarkerPlacement] = field(default_factory=list)
    marker_reassociations: dict[int, int] = field(default_factory=dict)
    figure_paths: dict[int, str] = field(default_factory=dict)
    figure_hashes: dict[int, str] = field(default_factory=dict)
    skip_indices: set[int] = field(default_factory=set)


class ResolvedPageBuilder:
    def __init__(self, resolved_dir: Path) -> None:
        self.resolved_dir = ensure_dir(resolved_dir)
        self.normalizer = FigureMarkerNormalizer()
        self._file_resolver = FigureResolver(resolved_dir)

    @staticmethod
    def _digest_obj(obj: Any) -> str:
        payload = json.dumps(obj, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def build(self, inp: ResolvedPageInput) -> tuple[str, str]:
        text = inp.canonical_md

        # 1) Syntax repairs on loose markers
        repairs_by_index = {r.figure_index: r for r in inp.marker_repairs}
        loose = self.normalizer.find_markers(text)
        to_apply: list = []
        for m in loose:
            rec = repairs_by_index.get(m.index)
            if rec:
                to_apply.append(
                    type(m)(
                        start=m.start,
                        end=m.end,
                        page=m.page,
                        index=m.index,
                        original=m.original,
                        normalized=rec.normalized,
                        is_strict=True,
                    )
                )
            elif not m.is_strict and m.index not in inp.skip_indices:
                to_apply.append(m)
        text = self.normalizer.apply_repairs(text, to_apply)

        # 2) Manual reassociation: rewrite marker index in place
        for md_idx, fig_idx in sorted(
            inp.marker_reassociations.items(), key=lambda x: x[0], reverse=True
        ):
            for m in self.normalizer.find_markers(text):
                if m.index == md_idx:
                    new_m = canonical_marker(inp.page_number, fig_idx)
                    text = text[: m.start] + new_m + text[m.end :]
                    break

        # 3) Manual insertions (sorted desc by offset)
        for pl in sorted(inp.manual_placements, key=lambda p: p.char_offset, reverse=True):
            ins = canonical_marker(pl.page_number, pl.figure_index)
            text = text[: pl.char_offset] + ins + "\n\n" + text[pl.char_offset :]

        # 4) Replace markers with image links; remove skipped
        def repl(match: re.Match[str]) -> str:
            pg = int(match.group(1))
            idx = int(match.group(2))
            if pg != inp.page_number:
                return match.group(0)
            if idx in inp.skip_indices:
                return ""
            path = inp.figure_paths.get(idx)
            if not path:
                return match.group(0)
            ext = Path(path).suffix or ".png"
            return marker_to_image_md(inp.page_number, idx, ext)

        resolved = FIGURE_MARKER_RE.sub(repl, text)

        canon_hash = text_sha256(inp.canonical_md)
        repair_digest = self._digest_obj(
            [{"i": r.figure_index, "o": r.original, "n": r.normalized} for r in inp.marker_repairs]
        )
        placement_digest = self._digest_obj(
            [
                {
                    "i": p.figure_index,
                    "o": p.char_offset,
                    "b": p.before_context,
                    "a": p.after_context,
                }
                for p in inp.manual_placements
            ]
        )
        skip_digest = self._digest_obj(sorted(inp.skip_indices))
        r_hash = resolved_page_hash(
            canonical_md_hash=canon_hash,
            figure_hashes=[inp.figure_hashes[i] for i in sorted(inp.figure_paths)],
            resolver_version=RESOLVER_VERSION,
            marker_repair_digest=repair_digest,
            placement_digest=placement_digest,
            skip_digest=skip_digest,
        )
        return resolved, r_hash

    def write_resolved(
        self, page_number: int, content: str, *, force: bool = False
    ) -> Path:
        return self._file_resolver.write_resolved(page_number, content, force=force)

    def copy_canonical(self, page_number: int, canonical_md: str) -> Path:
        return self._file_resolver.copy_canonical(page_number, canonical_md)
