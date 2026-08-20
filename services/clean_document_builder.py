"""Build clean_traced.md and clean.md from accepted clean_pages."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from core.cleaner_models import CLEAN_DOCUMENT_BUILDER_VERSION, CleanDocumentResult
from services.assembled_markdown_validator import PAGE_MARKER_RE
from utils.hashing import file_sha256, text_sha256
from utils.paths import ensure_dir


class CleanDocumentBuilder:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.clean_pages = project_root / "clean_pages"
        self.intermediate = ensure_dir(project_root / "intermediate")
        self.traced_path = self.intermediate / "clean_traced.md"
        self.clean_path = self.intermediate / "clean.md"
        self.version = CLEAN_DOCUMENT_BUILDER_VERSION

    def build(
        self,
        page_numbers: list[int],
        *,
        force: bool = False,
    ) -> CleanDocumentResult:
        missing = [
            p
            for p in page_numbers
            if not (self.clean_pages / f"page_{p:04d}.md").exists()
        ]
        if missing:
            return CleanDocumentResult(
                success=False,
                error=f"missing_clean_pages:{missing}",
            )

        page_hashes = []
        fragments = []
        for p in sorted(page_numbers):
            path = self.clean_pages / f"page_{p:04d}.md"
            body = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
            body = body.strip("\n")
            page_hashes.append(file_sha256(path))
            fragments.append(f"<!-- PAGE: {p:04d} -->\n\n{body}\n")

        traced = "\n".join(fragments)
        if not traced.endswith("\n"):
            traced += "\n"

        traced_hash = text_sha256(traced)
        doc_hash = hashlib.sha256(
            f"{traced_hash}|{self.version}|markers=1".encode("utf-8")
        ).hexdigest()
        clean_body = PAGE_MARKER_RE.sub("", traced)
        # collapse excessive blank lines created by marker removal
        clean_body = re.sub(r"\n{3,}", "\n\n", clean_body).strip("\n") + "\n"
        clean_hash = hashlib.sha256(
            f"{traced_hash}|strip_page_markers|v1".encode("utf-8")
        ).hexdigest()

        side = self.intermediate / "clean_document_hash.txt"
        if (
            not force
            and self.traced_path.exists()
            and self.clean_path.exists()
            and side.exists()
            and side.read_text(encoding="utf-8").strip() == f"{doc_hash}:{clean_hash}"
        ):
            return CleanDocumentResult(
                success=True,
                clean_traced_path=self.traced_path,
                clean_path=self.clean_path,
                cached=True,
                page_count=len(page_numbers),
                document_hash=doc_hash,
            )

        # atomic write traced
        tmp_t = self.traced_path.with_suffix(".md.tmp")
        tmp_t.write_text(traced, encoding="utf-8", newline="\n")
        tmp_t.replace(self.traced_path)

        tmp_c = self.clean_path.with_suffix(".md.tmp")
        tmp_c.write_text(clean_body, encoding="utf-8", newline="\n")
        # validate before replace is caller's job; still write atomically
        tmp_c.replace(self.clean_path)

        side.write_text(
            f"{doc_hash}:{clean_hash}\n", encoding="utf-8", newline="\n"
        )
        return CleanDocumentResult(
            success=True,
            clean_traced_path=self.traced_path,
            clean_path=self.clean_path,
            cached=False,
            page_count=len(page_numbers),
            document_hash=doc_hash,
        )
