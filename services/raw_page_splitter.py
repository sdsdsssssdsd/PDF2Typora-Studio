"""Split intermediate/raw.md into page fragments by PAGE markers."""

from __future__ import annotations

import re
from pathlib import Path

from core.cleaner_models import RawPageFragment
from utils.hashing import text_sha256

PAGE_MARKER_RE = re.compile(r"<!--\s*PAGE:\s*(\d+)\s*-->", re.IGNORECASE)


class RawPageSplitter:
    def split_text(self, raw_md: str) -> list[RawPageFragment]:
        text = (raw_md or "").replace("\r\n", "\n").replace("\r", "\n")
        matches = list(PAGE_MARKER_RE.finditer(text))
        if not matches:
            body = text.strip("\n")
            return [
                RawPageFragment(
                    page_number=1,
                    body=body,
                    source_hash=text_sha256(body),
                    marker="",
                )
            ]

        out: list[RawPageFragment] = []
        for i, m in enumerate(matches):
            page = int(m.group(1))
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[start:end]
            # strip leading/trailing blank lines only — do not rewrite content
            body = body.lstrip("\n").rstrip("\n")
            out.append(
                RawPageFragment(
                    page_number=page,
                    body=body,
                    source_hash=text_sha256(body),
                    marker=m.group(0),
                )
            )
        return out

    def split_file(self, raw_path: Path) -> list[RawPageFragment]:
        return self.split_text(raw_path.read_text(encoding="utf-8"))
