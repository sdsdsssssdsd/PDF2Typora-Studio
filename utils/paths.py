"""Path utilities."""

from __future__ import annotations

import re
from pathlib import Path


def sanitize_book_name(name: str) -> str:
    """Turn a PDF filename stem into a safe workspace directory name."""
    stem = Path(name).stem if "." in name else name
    stem = stem.strip()
    stem = re.sub(r'[<>:"/\\|?*]', "_", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem or "untitled"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def page_image_name(page_number: int) -> str:
    return f"page_{page_number:04d}.png"
