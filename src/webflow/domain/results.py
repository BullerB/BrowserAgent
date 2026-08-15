"""Structured output harvested from a goal page."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class ExtractedRecord(BaseModel):
    """One row of the result, e.g. a single insurance quote."""

    data: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0
    raw_text: str | None = None


class ResultSet(BaseModel):
    provider_id: str
    goal: str
    schema_name: str | None = None
    source_url: str | None = None
    records: list[ExtractedRecord] = Field(default_factory=list)
    """How the records were obtained: "llm", "heuristic" or "provider"."""
    method: str = "llm"
    warnings: list[str] = Field(default_factory=list)
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_empty(self) -> bool:
        return not self.records
