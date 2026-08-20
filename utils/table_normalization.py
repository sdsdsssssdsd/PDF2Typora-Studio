"""Normalize Markdown tables for cell-content comparison."""

from __future__ import annotations

import re


_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")


def extract_table_cell_payloads(text: str) -> list[str]:
    cells: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") and stripped.count("|") < 2:
            continue
        if _TABLE_SEP.match(stripped):
            continue
        # split cells
        parts = [c.strip() for c in stripped.strip("|").split("|")]
        for c in parts:
            if c:
                cells.append(re.sub(r"\s+", " ", c))
    return cells


def table_payloads_equivalent(source: str, cleaned: str) -> bool:
    return extract_table_cell_payloads(source) == extract_table_cell_payloads(cleaned)
