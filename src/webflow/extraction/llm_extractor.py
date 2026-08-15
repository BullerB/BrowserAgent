"""LLM-based extraction of structured records from a results page."""

from __future__ import annotations

from typing import Any

from playwright.async_api import Page
from pydantic import BaseModel, Field

from webflow.domain.results import ExtractedRecord, ResultSet
from webflow.extraction.schema import ResultSchema
from webflow.llm.base import LLMClient
from webflow.logging import get_logger

log = get_logger(__name__)

EXTRACTION_SYSTEM_PROMPT = """\
You extract structured records from the text of a web page.

Return every record that matches the schema, in the order they appear. Copy
values exactly as shown - do not convert currencies, do not compute, do not
invent fields that are not on the page. Use null for anything missing. If the
page shows no matching records, return an empty list and say why in `notes`.

Answer with JSON only.\
"""


class ExtractionResponse(BaseModel):
    records: list[dict[str, Any]] = Field(default_factory=list)
    notes: str | None = None


async def page_text(page: Page, limit: int = 12_000) -> str:
    try:
        text: str = await page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception:
        text = ""
    return text[:limit]


async def extract_with_llm(
    page: Page,
    llm: LLMClient,
    schema: ResultSchema,
    provider_id: str,
    goal: str,
) -> ResultSet:
    text = await page_text(page)
    user = f"{schema.to_prompt()}\n\nPAGE URL: {page.url}\n\nPAGE TEXT:\n{text}"

    response = await llm.generate_structured(EXTRACTION_SYSTEM_PROMPT, user, ExtractionResponse)

    results = ResultSet(
        provider_id=provider_id,
        goal=goal,
        schema_name=schema.name,
        source_url=page.url,
        method="llm",
        records=[ExtractedRecord(data=r, confidence=0.9) for r in response.records],
    )
    if response.notes:
        results.warnings.append(response.notes)
    log.info("llm_extraction", provider=provider_id, records=len(results.records))
    return results
