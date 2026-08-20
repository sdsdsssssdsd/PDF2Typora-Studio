"""Pydantic schemas for structured page transcription."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FigureDetection(BaseModel):
    """A visual figure detected on a PDF page."""

    model_config = ConfigDict(extra="forbid")

    figure_index: int = Field(ge=1)
    marker: str
    figure_type: Literal[
        "figure",
        "diagram",
        "chart",
        "photo",
        "illustration",
        "geometry",
        "plot",
        "flowchart",
        "unknown",
    ] = "unknown"
    caption: str | None = None
    bbox_1000: tuple[int, int, int, int] | None = None
    mermaid_candidate: bool = False
    needs_review: bool = True

    @field_validator("bbox_1000")
    @classmethod
    def _check_bbox(cls, v: tuple[int, int, int, int] | None):
        if v is None:
            return v
        if len(v) != 4:
            raise ValueError("bbox_1000 values must have 4 ints")
        for n in v:
            if n < 0 or n > 1000:
                raise ValueError("bbox_1000 values must be in 0..1000")
        return v


class PageTranscriptionResult(BaseModel):
    """Structured transcription result for one rendered PDF page."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    page_number: int = Field(ge=1)
    printed_page_label: str | None = None
    markdown: str = ""
    figures: list[FigureDetection] = Field(default_factory=list)
    continues_from_previous: bool = False
    continues_to_next: bool = False
    warnings: list[str] = Field(default_factory=list)
    needs_review: bool = False


TRANSCRIPTION_SCHEMA_VERSION = "1.0"
TRANSCRIPTION_PIPELINE_VERSION = "5"
VALIDATOR_VERSION = "5.0"
