"""Generic quote scraper for a forsikringsguiden.dk results page.

Since the exact DOM of the results page is only known once you've actually
run a flow (which needs real personal/property data), this uses resilient
heuristics: it scans for elements that contain a Danish kroner amount and
walks up to the nearest ancestor that also contains a company name, rather
than relying on brittle hardcoded selectors.
"""
from __future__ import annotations

import re

from playwright.sync_api import Page

PRICE_RE = re.compile(r"(\d[\d\.]*)\s*kr", re.IGNORECASE)


def extract_quotes(page: Page) -> list[dict]:
    price_locator = page.locator("text=/kr\\.?\\s*\\/?\\s*(år|md|maaned|måned)?/i")
    for _ in range(10):  # poll up to ~10s for async price calculations to render
        if price_locator.count() > 0:
            break
        page.wait_for_timeout(1000)
    page.wait_for_timeout(1000)  # let remaining prices on the page settle

    quotes: list[dict] = []
    seen_texts: set[str] = set()

    candidates = price_locator.all()

    for element in candidates:
        try:
            if not element.is_visible():
                continue
            card = element
            for _ in range(4):
                parent = card.locator("xpath=..")
                if parent.count() == 0:
                    break
                card = parent

            card_text = card.inner_text().strip()
            if not card_text or card_text in seen_texts:
                continue
            seen_texts.add(card_text)

            price_match = PRICE_RE.search(card_text)
            if not price_match:
                continue

            lines = [line.strip() for line in card_text.splitlines() if line.strip()]
            company = lines[0] if lines else "Unknown"

            quotes.append(
                {
                    "company": company,
                    "price_text": price_match.group(0),
                    "raw_text": card_text,
                }
            )
        except Exception:
            continue

    return quotes
