"""forsikringsguiden.dk - Danish insurance comparison site.

Each product is a long multi-page form ending in a list of quotes. Goals map
one-to-one onto those products.
"""

from __future__ import annotations

import re
from contextlib import suppress

from playwright.async_api import Page

from providers.base import Goal, ProviderPlugin
from webflow.extraction.schema import QUOTE_SCHEMA

BASE_URL = "https://forsikringsguiden.dk"

#: The consent dialog is injected by a script a moment after the page loads, so
#: it has to be waited for rather than looked up once.
ACCEPT_COOKIES_RE = re.compile(r"accepter\s+alle", re.IGNORECASE)
COOKIE_WAIT_MS = 10_000

COMMON_HINTS = (
    "The site is in Danish. 'Videre' / 'Næste' means continue, 'Tilbage' means back.",
    "Accept the cookie banner ('ACCEPTER ALLE COOKIES') before touching the form.",
    "The address field is an autocomplete: type part of the address, then pick a "
    "suggestion from the list - use fill_and_pick.",
    "Answer every question on a page before pressing 'Videre'; the site validates "
    "per page and will not advance otherwise.",
    "Questions like 'antal skader' (number of claims) are usually radio buttons "
    "labelled 0, 1, 2 - not text fields.",
)


class ForsikringsguidenProvider(ProviderPlugin):
    id = "forsikringsguiden"
    name = "Forsikringsguiden (DK)"
    base_url = BASE_URL

    @property
    def goals(self) -> dict[str, Goal]:
        return {
            goal.name: goal
            for goal in (
                Goal(
                    name="bilforsikring",
                    description=(
                        "Get car insurance quotes: fill in the driver, address and "
                        "vehicle details until the comparison list of prices is shown."
                    ),
                    start_url=f"{BASE_URL}/bilforsikring",
                    result_schema=QUOTE_SCHEMA,
                    required_profile_keys=(
                        "person.address",
                        "person.birth_date",
                        "vehicle.registration_number",
                    ),
                    hints=(
                        *COMMON_HINTS,
                        "The registration number ('nummerplade') lookup fills in the "
                        "car model automatically - wait for it before continuing.",
                    ),
                ),
                Goal(
                    name="indboforsikring",
                    description="Get home contents insurance quotes.",
                    start_url=f"{BASE_URL}/indboforsikring",
                    result_schema=QUOTE_SCHEMA,
                    required_profile_keys=("person.address", "home.home_type"),
                    hints=COMMON_HINTS,
                ),
                Goal(
                    name="husforsikring",
                    description="Get house/building insurance quotes.",
                    start_url=f"{BASE_URL}/husforsikring",
                    result_schema=QUOTE_SCHEMA,
                    required_profile_keys=("home.address", "home.construction_year"),
                    hints=COMMON_HINTS,
                ),
                Goal(
                    name="ulykkesforsikring",
                    description="Get accident insurance quotes.",
                    start_url=f"{BASE_URL}/ulykkesforsikring",
                    result_schema=QUOTE_SCHEMA,
                    required_profile_keys=("person.birth_date",),
                    hints=COMMON_HINTS,
                ),
            )
        }

    async def prepare(self, page: Page) -> None:
        """Dismiss the cookie wall before the planner spends a turn on it."""
        button = page.locator("button").filter(has_text=ACCEPT_COOKIES_RE)
        try:
            await button.first.wait_for(state="visible", timeout=COOKIE_WAIT_MS)
            await button.first.click(timeout=5_000)
            await button.first.wait_for(state="hidden", timeout=5_000)
        except Exception:
            # No banner, or it changed shape - the planner can still handle it.
            return

    async def before_extract(self, page: Page) -> None:
        """Prices are calculated per insurer asynchronously; let them land."""
        with suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=15_000)
        await page.wait_for_timeout(2_000)

    async def is_results_page(self, page: Page) -> bool:
        try:
            return await page.locator("text=/\\d[\\d.]*\\s*kr/i").count() > 0
        except Exception:
            return False


PROVIDER = ForsikringsguidenProvider()
