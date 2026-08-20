"""Normalize math delimiters for semantic payload comparison."""

from __future__ import annotations

import re
import unicodedata


_INLINE_DOLLAR = re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", re.DOTALL)
_DISPLAY_DOLLAR = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_PAREN = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
_BRACKET = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)
_ALIGNED = re.compile(
    r"\\begin\{aligned\}(.*?)\\end\{aligned\}",
    re.DOTALL | re.IGNORECASE,
)


def extract_math_payloads(text: str) -> list[str]:
    payloads: list[str] = []
    for rx in (_DISPLAY_DOLLAR, _INLINE_DOLLAR, _BRACKET, _PAREN):
        for m in rx.finditer(text or ""):
            payloads.append(normalize_math_payload(m.group(1)))
    return payloads


def normalize_math_payload(expr: str) -> str:
    s = unicodedata.normalize("NFKC", expr or "")
    s = _ALIGNED.sub(lambda m: m.group(1), s)
    s = s.replace("&", " ")
    s = s.replace("\\\\", " ")
    s = re.sub(r"\s+", "", s)
    return s


def math_payloads_equivalent(source: str, cleaned: str) -> bool:
    a = extract_math_payloads(source)
    b = extract_math_payloads(cleaned)
    # Order-insensitive multiset compare after normalization
    return sorted(a) == sorted(b)
