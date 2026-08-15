"""A throwaway provider pointing at the local fixture page."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

from playwright.async_api import Page

from providers.base import Goal, ProviderPlugin
from webflow.extraction.schema import QUOTE_SCHEMA


class DemoProvider(ProviderPlugin):
    id = "demo"
    name = "Demo insurance comparison"

    def __init__(self, start_url: str, flows_dir: Path) -> None:
        self.base_url = start_url
        self._start_url = start_url
        self._flows_dir = flows_dir

    @property
    def flows_dir(self) -> Path:
        return self._flows_dir

    @property
    def goals(self) -> dict[str, Goal]:
        return {
            "quote": Goal(
                name="quote",
                description="Fill the form until the quote list appears.",
                start_url=self._start_url,
                result_schema=QUOTE_SCHEMA,
                required_profile_keys=("person.email",),
            )
        }

    async def before_extract(self, page: Page) -> None:
        with suppress(Exception):
            await page.wait_for_selector(".quote", timeout=3_000)
