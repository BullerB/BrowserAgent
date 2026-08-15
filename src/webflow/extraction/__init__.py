"""Turning a results page into structured records."""

from __future__ import annotations

from playwright.async_api import Page

from webflow.domain.results import ResultSet
from webflow.extraction.heuristics import extract_by_heuristic
from webflow.extraction.llm_extractor import extract_with_llm
from webflow.extraction.schema import (
    BUILTIN_SCHEMAS,
    QUOTE_SCHEMA,
    ResultField,
    ResultSchema,
)
from webflow.llm.base import LLMClient
from webflow.logging import get_logger

log = get_logger(__name__)

__all__ = [
    "BUILTIN_SCHEMAS",
    "QUOTE_SCHEMA",
    "ResultField",
    "ResultSchema",
    "extract",
    "extract_by_heuristic",
    "extract_with_llm",
]


async def extract(
    page: Page,
    *,
    provider_id: str,
    goal: str,
    schema: ResultSchema | None = None,
    llm: LLMClient | None = None,
) -> ResultSet:
    """Prefer the LLM extractor, fall back to price heuristics.

    The fallback also fires when the LLM finds nothing, since an empty result is
    usually a sign the page text was structured in a way the model discarded.
    """
    if llm is not None and schema is not None:
        try:
            results = await extract_with_llm(page, llm, schema, provider_id, goal)
            if not results.is_empty:
                return results
            log.info("llm_extraction_empty_falling_back", provider=provider_id)
        except Exception as exc:
            log.warning("llm_extraction_failed", provider=provider_id, error=str(exc))

    return await extract_by_heuristic(page, provider_id, goal)
