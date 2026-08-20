"""Sanitize erroneous literal escape sequences in model Markdown."""

from __future__ import annotations

import re

# LaTeX commands that begin with \n — must NOT become newlines
_LATEX_N_CMD = re.compile(
    r"\\n(?:"
    r"eq|u|abla|otin|ot|i|mid|approx|cong|sim|less|gtr|leq|geq|"
    r"subseteq|supseteq|rightarrow|leftarrow|mapsto|Rightarrow|"
    r"Leftarrow|ewline|ewpage|oindent|ormalsize|atural|ull|"
    r"eg|ew|ext|umber|ode"
    r")\b"
)

_PROTECTED = re.compile(r"\x00L(\d+)\x00")


class MarkdownEscapeSanitizer:
    """Convert erroneous `\\n` text into real newlines without harming LaTeX."""

    version = "1"

    def sanitize(self, text: str) -> str:
        if not text or "\\" not in text:
            return text

        placeholders: list[str] = []

        def _protect(match: re.Match[str]) -> str:
            placeholders.append(match.group(0))
            return f"\x00L{len(placeholders) - 1}\x00"

        out = _LATEX_N_CMD.sub(_protect, text)
        # protect double backslash
        out = out.replace("\\\\", "\x00BS\x00")
        # literal backslash-n / backslash-r / backslash-t (not LaTeX)
        out = out.replace("\\r\\n", "\n")
        out = out.replace("\\n", "\n")
        out = out.replace("\\r", "\n")
        out = out.replace("\\t", "\t")
        out = out.replace("\x00BS\x00", "\\\\")

        def _restore(match: re.Match[str]) -> str:
            return placeholders[int(match.group(1))]

        return _PROTECTED.sub(_restore, out)

    def needs_sanitize(self, text: str) -> bool:
        if "\\n" not in text and "\\t" not in text and "\\r" not in text:
            return False
        probe = self.sanitize(text)
        return probe != text
