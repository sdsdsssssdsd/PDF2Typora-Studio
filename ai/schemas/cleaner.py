"""Pydantic schema for AI Markdown cleaner structured output."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CleanPageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    page_number: int = Field(ge=1)
    cleaned_markdown: str
    warnings: list[str] = Field(default_factory=list)
    needs_review: bool = False
    change_categories: list[str] = Field(default_factory=list)


CLEAN_PAGE_JSON_SCHEMA = CleanPageResult.model_json_schema()
