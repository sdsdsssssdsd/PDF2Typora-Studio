"""Pipeline orchestration (Phase 5+ stub)."""

from __future__ import annotations

from core.models import PipelineState


class Pipeline:
    """Full conversion pipeline — implemented in later phases."""

    def __init__(self) -> None:
        self.state = PipelineState.IDLE

    def reset(self) -> None:
        self.state = PipelineState.IDLE
