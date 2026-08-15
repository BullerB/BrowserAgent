"""Provider plugin contract.

A provider is "a website we know how to talk to". It contributes goals, the
profile fields those goals need, optional page hooks, and a directory of
committed flows. Everything else - planning, checkpoints, recording, extraction
- is generic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import Page

from webflow.extraction.schema import ResultSchema


@dataclass(frozen=True, slots=True)
class Goal:
    """One thing a user can ask a provider for."""

    name: str
    description: str
    start_url: str
    result_schema: ResultSchema
    """Profile keys without which the run will certainly stop for a human."""
    required_profile_keys: tuple[str, ...] = ()
    """Site-specific advice handed to the planner, e.g. quirks of a widget."""
    hints: tuple[str, ...] = ()


@dataclass
class ProviderPlugin(ABC):
    """Base class for site plugins."""

    id: str = field(init=False)
    name: str = field(init=False)
    base_url: str = field(init=False)
    """Extra domains the guards should allow, e.g. an SSO host."""
    extra_allowed_domains: tuple[str, ...] = field(init=False, default=())

    @property
    @abstractmethod
    def goals(self) -> dict[str, Goal]: ...

    @property
    def flows_dir(self) -> Path:
        """Committed flows shipped alongside the plugin module."""
        module_file = Path(str(type(self).__module__.replace(".", "/")))
        del module_file
        import inspect

        return Path(inspect.getfile(type(self))).parent / "flows"

    def goal(self, name: str) -> Goal:
        if name not in self.goals:
            raise KeyError(f"{self.id} has no goal {name!r}. Known: {sorted(self.goals)}")
        return self.goals[name]

    async def prepare(self, page: Page) -> None:  # noqa: B027 - optional hook
        """Run once after the start URL loads - cookie walls, region pickers, ..."""

    async def before_extract(self, page: Page) -> None:  # noqa: B027 - optional hook
        """Run once the results page is reached, e.g. wait for async prices."""

    async def is_results_page(self, page: Page) -> bool:
        """Optional cheap check so replay can confirm it reached the goal."""
        return True
