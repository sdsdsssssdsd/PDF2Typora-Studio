"""Rule-based continuity detection between consecutive pages (no AI)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.assemble_models import ContinuityCandidate, ContinuityCandidateStatus
from utils.logger import get_logger

logger = get_logger("continuity_analyzer")

_TERMINAL = re.compile(r"[.!?…。！？]$")
_LOWER_START = re.compile(r"^[a-z]")
_MATH_OPEN = re.compile(r"(?<!\\)(\$\$|\\\(|\\\[)\s*$")
_TABLE_ROW = re.compile(r"^\s*\|")


class ContinuityAnalyzer:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = (config or {}).get("continuity") or config or {}
        self.tail_chars = int(cfg.get("tail_chars", 500))
        self.head_chars = int(cfg.get("head_chars", 500))
        self.enable_heuristics = bool(cfg.get("enable_heuristics", True))
        self.version = str(cfg.get("analyzer_version", "1"))

    def analyze_project(
        self,
        *,
        project_root: Path,
        page_numbers: list[int],
        page_texts: dict[int, str] | None = None,
    ) -> list[ContinuityCandidate]:
        texts = page_texts or {}
        ordered = sorted(page_numbers)
        out: list[ContinuityCandidate] = []
        for i in range(len(ordered) - 1):
            left, right = ordered[i], ordered[i + 1]
            left_text = texts.get(left) or self._load_text(project_root, left)
            right_text = texts.get(right) or self._load_text(project_root, right)
            cand = self.analyze_pair(
                left_page=left,
                right_page=right,
                left_text=left_text,
                right_text=right_text,
                left_flags=self._load_flags(project_root, left),
                right_flags=self._load_flags(project_root, right),
            )
            if cand is not None:
                out.append(cand)
        return out

    def analyze_pair(
        self,
        *,
        left_page: int,
        right_page: int,
        left_text: str,
        right_text: str,
        left_flags: dict[str, Any] | None = None,
        right_flags: dict[str, Any] | None = None,
    ) -> ContinuityCandidate | None:
        left_flags = left_flags or {}
        right_flags = right_flags or {}
        flags: list[str] = []
        score = 0.0

        if left_flags.get("continues_to_next"):
            flags.append("continues_to_next")
            score += 1.0
        if right_flags.get("continues_from_previous"):
            flags.append("continues_from_previous")
            score += 1.0

        left_body = self._strip_page_marker(left_text).rstrip()
        right_body = self._strip_page_marker(right_text).lstrip()
        tail = left_body[-self.tail_chars :] if left_body else ""
        head = right_body[: self.head_chars] if right_body else ""

        if self.enable_heuristics:
            last_line = tail.splitlines()[-1].strip() if tail else ""
            first_line = head.splitlines()[0].strip() if head else ""
            if last_line and not _TERMINAL.search(last_line):
                # only mild suspicion — titles/lists often lack terminals
                score += 0.2
                flags.append("no_terminal_punct")
            if first_line and _LOWER_START.match(first_line):
                score += 0.5
                flags.append("next_starts_lowercase")
            if last_line and _MATH_OPEN.search(last_line):
                score += 0.6
                flags.append("unclosed_math")
            if last_line and _TABLE_ROW.match(last_line) and first_line and _TABLE_ROW.match(first_line):
                score += 0.4
                flags.append("table_continuation")

        # Candidate only if AI flags or stronger heuristics
        if not flags:
            return None
        if set(flags) <= {"no_terminal_punct"} and score < 0.5:
            return None

        return ContinuityCandidate(
            left_page=left_page,
            right_page=right_page,
            left_tail=tail[-200:],
            right_head=head[:200],
            source_flags=flags,
            suspicion_score=round(score, 3),
            status=ContinuityCandidateStatus.UNREVIEWED.value,
        )

    @staticmethod
    def _strip_page_marker(text: str) -> str:
        return re.sub(
            r"<!--\s*PAGE:\s*\d+\s*-->\s*",
            "",
            text or "",
            count=1,
            flags=re.IGNORECASE,
        )

    def _load_text(self, project_root: Path, page: int) -> str:
        for folder in ("resolved_pages", "markdown_pages"):
            path = project_root / folder / f"page_{page:04d}.md"
            if path.exists():
                return path.read_text(encoding="utf-8")
        return ""

    def _load_flags(self, project_root: Path, page: int) -> dict[str, Any]:
        js = project_root / "page_results" / f"page_{page:04d}.json"
        if not js.exists():
            return {}
        try:
            payload = json.loads(js.read_text(encoding="utf-8"))
            result = payload.get("result") or {}
            return {
                "continues_from_previous": bool(
                    result.get("continues_from_previous")
                ),
                "continues_to_next": bool(result.get("continues_to_next")),
            }
        except (json.JSONDecodeError, OSError):
            return {}
