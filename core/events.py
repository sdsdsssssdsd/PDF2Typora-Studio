"""Application events (Phase 5+ stub)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AppEvent:
    name: str
    payload: dict[str, Any] | None = None
