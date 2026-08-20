"""Deterministic Markdown format cleaner (no AI)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.cleaner_models import DETERMINISTIC_CLEANER_VERSION, DeterministicCleanResult

_HR_LINE = re.compile(r"^---\s*$")
_OUTER_FENCE_OPEN = re.compile(r"^```(?:markdown|md)?\s*$", re.IGNORECASE)
_FENCE_LINE = re.compile(r"^```")


class DeterministicCleaner:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = (config or {}).get("cleaner") or config or {}
        rules = cfg.get("rules") or {}
        self.remove_printed = bool(rules.get("remove_printed_page_label", True))
        self.remove_hr = bool(rules.get("remove_horizontal_rules", True))
        self.convert_paren = bool(rules.get("convert_parenthesis_math", True))
        self.convert_bracket = bool(rules.get("convert_bracket_math", True))
        self.remove_outer_fence = bool(rules.get("remove_outer_markdown_fence", True))
        self.version = DETERMINISTIC_CLEANER_VERSION

    def clean(
        self,
        *,
        page_number: int,
        body: str,
        printed_page_label: str | None = None,
    ) -> DeterministicCleanResult:
        text = body.replace("\r\n", "\n").replace("\r", "\n")
        actions: list[dict] = []
        warnings: list[str] = []
        issues: list[str] = []

        if self.remove_outer_fence:
            text, act = self._strip_outer_markdown_fence(text)
            if act:
                actions.append(act)

        if self.remove_printed and printed_page_label:
            text, acts = self._remove_printed_label(text, printed_page_label.strip())
            actions.extend(acts)

        if self.remove_hr:
            text, acts = self._remove_horizontal_rules(text)
            actions.extend(acts)

        if self.convert_paren or self.convert_bracket:
            text, acts, iss = self._convert_math_delimiters(text)
            actions.extend(acts)
            issues.extend(iss)

        return DeterministicCleanResult(
            page_number=page_number,
            cleaned=text,
            actions=actions,
            warnings=warnings,
            issues=issues,
        )

    def _strip_outer_markdown_fence(self, text: str) -> tuple[str, dict | None]:
        lines = text.split("\n")
        # trim empty edges
        while lines and not lines[0].strip():
            lines = lines[1:]
        while lines and not lines[-1].strip():
            lines = lines[:-1]
        if len(lines) < 2:
            return text, None
        if not _OUTER_FENCE_OPEN.match(lines[0].strip()):
            return text, None
        if not _FENCE_LINE.match(lines[-1].strip()):
            return text, None
        # ensure no other fence lines that would make this ambiguous? allow inner fences
        inner = "\n".join(lines[1:-1])
        return inner, {
            "action": "remove_outer_markdown_fence",
            "detail": "stripped page-level ```markdown wrapper",
        }

    def _remove_printed_label(
        self, text: str, label: str
    ) -> tuple[str, list[dict]]:
        if not label:
            return text, []
        lines = text.split("\n")
        actions: list[dict] = []
        # only first 3 and last 3 non-empty-ish lines
        candidates: list[int] = []
        nonempty = [i for i, ln in enumerate(lines) if ln.strip()]
        head = nonempty[:3]
        tail = nonempty[-3:] if len(nonempty) > 3 else []
        for i in head + tail:
            if lines[i].strip() == label:
                candidates.append(i)
        # unique preserve order
        seen: set[int] = set()
        drop: list[int] = []
        for i in candidates:
            if i not in seen:
                seen.add(i)
                drop.append(i)
                actions.append(
                    {
                        "action": "remove_printed_page_label",
                        "original_line": lines[i],
                        "position": i,
                    }
                )
        if not drop:
            return text, []
        new_lines = [ln for i, ln in enumerate(lines) if i not in set(drop)]
        return "\n".join(new_lines), actions

    def _remove_horizontal_rules(self, text: str) -> tuple[str, list[dict]]:
        lines = text.split("\n")
        actions: list[dict] = []
        out: list[str] = []
        in_fence = False
        for i, ln in enumerate(lines):
            if _FENCE_LINE.match(ln.strip()):
                in_fence = not in_fence
                out.append(ln)
                continue
            if not in_fence and _HR_LINE.match(ln.strip()):
                actions.append(
                    {
                        "action": "remove_horizontal_rule",
                        "original_line": ln,
                        "position": i,
                    }
                )
                continue
            out.append(ln)
        return "\n".join(out), actions

    def _convert_math_delimiters(
        self, text: str
    ) -> tuple[str, list[dict], list[str]]:
        """Convert \\( \\) / \\[ \\] outside fences when paired."""
        actions: list[dict] = []
        issues: list[str] = []
        parts = self._split_fences(text)
        out: list[str] = []
        for kind, chunk in parts:
            if kind == "fence":
                out.append(chunk)
                continue
            new_chunk, acts, iss = self._convert_math_in_prose(chunk)
            out.append(new_chunk)
            actions.extend(acts)
            issues.extend(iss)
        return "".join(out), actions, issues

    def _convert_math_in_prose(
        self, text: str
    ) -> tuple[str, list[dict], list[str]]:
        actions: list[dict] = []
        issues: list[str] = []
        s = text

        if self.convert_bracket:
            # display \[ ... \]
            open_n = len(re.findall(r"\\\[", s))
            close_n = len(re.findall(r"\\\]", s))
            if open_n != close_n:
                issues.append("math_delimiter_issue")
            else:
                def repl_disp(m: re.Match[str]) -> str:
                    actions.append({"action": "convert_bracket_math"})
                    inner = m.group(1).strip("\n")
                    return f"$$\n{inner}\n$$"

                s = re.sub(r"\\\[(.*?)\\\]", repl_disp, s, flags=re.DOTALL)

        if self.convert_paren:
            open_n = len(re.findall(r"\\\(", s))
            close_n = len(re.findall(r"\\\)", s))
            if open_n != close_n:
                issues.append("math_delimiter_issue")
            else:
                def repl_inl(m: re.Match[str]) -> str:
                    actions.append({"action": "convert_parenthesis_math"})
                    return f"${m.group(1)}$"

                s = re.sub(r"\\\((.+?)\\\)", repl_inl, s, flags=re.DOTALL)

        return s, actions, sorted(set(issues))

    @staticmethod
    def _split_fences(text: str) -> list[tuple[str, str]]:
        lines = text.split("\n")
        parts: list[tuple[str, str]] = []
        buf: list[str] = []
        in_fence = False
        for ln in lines:
            if _FENCE_LINE.match(ln.strip()):
                if in_fence:
                    buf.append(ln)
                    parts.append(("fence", "\n".join(buf)))
                    buf = []
                    in_fence = False
                else:
                    if buf:
                        parts.append(("prose", "\n".join(buf) + "\n"))
                        buf = []
                    buf = [ln]
                    in_fence = True
                continue
            buf.append(ln)
        if buf:
            parts.append(("fence" if in_fence else "prose", "\n".join(buf)))
        return parts

    @staticmethod
    def load_printed_label(project_root: Path, page: int) -> str | None:
        js = project_root / "page_results" / f"page_{page:04d}.json"
        if not js.exists():
            return None
        try:
            payload = json.loads(js.read_text(encoding="utf-8"))
            result = payload.get("result") or {}
            label = result.get("printed_page_label")
            return str(label) if label else None
        except (json.JSONDecodeError, OSError):
            return None
