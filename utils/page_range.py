"""Page range expression parser (1-based page numbers)."""

from __future__ import annotations

import re


class PageRangeError(ValueError):
    """Invalid page range expression."""


_TOKEN_RE = re.compile(
    r"^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$"
)


def parse_page_range(expression: str, total_pages: int) -> list[int]:
    """Parse expressions like ``1,3,5-8`` into sorted unique 1-based pages.

    Raises:
        PageRangeError: on empty input, inverted ranges, out-of-bounds, or junk.
    """
    if total_pages < 1:
        raise PageRangeError("PDF has no pages")

    text = (expression or "").strip()
    if not text:
        raise PageRangeError("页面范围不能为空")

    pages: set[int] = set()
    for raw in text.split(","):
        part = raw.strip()
        if not part:
            raise PageRangeError("页面范围包含空段")
        m = _TOKEN_RE.match(part)
        if not m:
            raise PageRangeError(f"无法解析页码片段: {part!r}")
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) is not None else start
        if start < 1 or end < 1:
            raise PageRangeError(f"页码必须从 1 开始: {part!r}")
        if start > end:
            raise PageRangeError(f"页码范围颠倒: {part!r}")
        if start > total_pages or end > total_pages:
            raise PageRangeError(
                f"页码超出范围（PDF 共 {total_pages} 页）: {part!r}"
            )
        pages.update(range(start, end + 1))

    return sorted(pages)


def all_pages(total_pages: int) -> list[int]:
    if total_pages < 1:
        return []
    return list(range(1, total_pages + 1))
