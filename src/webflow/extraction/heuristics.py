"""Price-scanning fallback for results pages.

Ported from the original prototype: find kroner amounts, walk up to the card
that also names a company. It is crude, but it needs no LLM and it works on the
first run of a site nobody has described yet.
"""

from __future__ import annotations

import re

from playwright.async_api import Page

from webflow.domain.results import ExtractedRecord, ResultSet
from webflow.logging import get_logger

log = get_logger(__name__)

PRICE_RE = re.compile(r"(\d{1,3}(?:[.\s]\d{3})*(?:,\d+)?|\d+(?:[.,]\d+)?)\s*(kr|dkk|€|\$)", re.I)
PERIOD_RE = re.compile(r"\b(år|aar|md|måned|maaned|kvartal|year|month)\b", re.I)

_PERIOD_MAP = {
    "år": "year",
    "aar": "year",
    "year": "year",
    "md": "month",
    "måned": "month",
    "maaned": "month",
    "month": "month",
    "kvartal": "quarter",
}

CARD_SELECTOR = (
    "[class*='card'], [class*='result'], [class*='offer'], [class*='quote'], "
    "[class*='product'], li, tr, article"
)


def normalise_price(raw: str) -> str:
    """Danish thousands separators are dots; strip everything but digits."""
    return re.sub(r"[^\d]", "", raw.split(",")[0])


def parse_card(text: str) -> ExtractedRecord | None:
    match = PRICE_RE.search(text)
    if not match:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    period = PERIOD_RE.search(text)
    return ExtractedRecord(
        data={
            "company": lines[0][:80],
            "price": normalise_price(match.group(1)),
            "currency": "DKK" if match.group(2).lower() in {"kr", "dkk"} else match.group(2),
            "period": _PERIOD_MAP.get(period.group(1).lower()) if period else None,
        },
        confidence=0.5,
        raw_text=text[:1_000],
    )


async def extract_by_heuristic(
    page: Page, provider_id: str, goal: str, *, max_cards: int = 60
) -> ResultSet:
    results = ResultSet(
        provider_id=provider_id, goal=goal, source_url=page.url, method="heuristic"
    )
    seen: set[str] = set()

    cards = page.locator(CARD_SELECTOR)
    count = min(await cards.count(), max_cards * 4)

    for index in range(count):
        if len(results.records) >= max_cards:
            break
        card = cards.nth(index)
        try:
            if not await card.is_visible():
                continue
            text = (await card.inner_text()).strip()
        except Exception:
            continue

        if not text or len(text) > 1_500 or text in seen:
            continue
        seen.add(text)

        record = parse_card(text)
        if record is not None:
            results.records.append(record)

    if not results.records:
        results.warnings.append("no kroner amounts found on the page")
    log.info("heuristic_extraction", provider=provider_id, records=len(results.records))
    return results
