"""Resilient element addressing.

The old prototype stored a single ``div:nth-of-type(3) > ... > button`` chain,
which breaks the moment the site re-renders. Here every element is recorded as
an *ordered set* of independent strategies; playback tries them best-first, and
the first one that resolves to a usable element wins.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class SelectorKind(StrEnum):
    """Ordered roughly by how well each survives a site redesign."""

    TEST_ID = "test_id"
    ROLE = "role"
    LABEL = "label"
    PLACEHOLDER = "placeholder"
    ELEMENT_ID = "element_id"
    NAME_ATTR = "name_attr"
    ALT_TEXT = "alt_text"
    TITLE = "title"
    TEXT = "text"
    CSS = "css"
    XPATH = "xpath"


#: Higher is better. Used to sort candidates and to score newly built sets.
KIND_PRIORITY: dict[SelectorKind, int] = {
    SelectorKind.TEST_ID: 100,
    SelectorKind.ROLE: 90,
    SelectorKind.LABEL: 85,
    SelectorKind.PLACEHOLDER: 70,
    SelectorKind.ELEMENT_ID: 65,
    SelectorKind.NAME_ATTR: 60,
    SelectorKind.ALT_TEXT: 50,
    SelectorKind.TITLE: 45,
    SelectorKind.TEXT: 40,
    SelectorKind.CSS: 20,
    SelectorKind.XPATH: 10,
}


#: Roles ``page.get_by_role`` understands. Anything else (e.g. our "generic"
#: fallback) must not become a ROLE candidate.
ROLE_SELECTABLE = frozenset(
    {
        "alert",
        "button",
        "checkbox",
        "combobox",
        "dialog",
        "heading",
        "link",
        "listbox",
        "menuitem",
        "option",
        "radio",
        "searchbox",
        "slider",
        "spinbutton",
        "switch",
        "tab",
        "textbox",
    }
)


class Selector(BaseModel):
    """A single addressing strategy."""

    kind: SelectorKind
    value: str
    """Accessible name, only meaningful for :attr:`SelectorKind.ROLE`."""
    name: str | None = None
    exact: bool = False
    """Disambiguates when a strategy legitimately matches several elements."""
    nth: int | None = None

    @property
    def priority(self) -> int:
        return KIND_PRIORITY[self.kind]

    def __str__(self) -> str:
        base = f"{self.kind.value}={self.value!r}"
        if self.name:
            base += f" name={self.name!r}"
        if self.nth is not None:
            base += f" [{self.nth}]"
        return base


class SelectorSet(BaseModel):
    """Everything we know about how to find one element."""

    candidates: list[Selector] = Field(default_factory=list)
    """Human-readable label used in logs, checkpoints and error messages."""
    description: str = ""
    """URL of the iframe the element lives in, if not the main frame."""
    frame_url: str | None = None

    def ordered(self) -> list[Selector]:
        """Candidates in the order they should be tried.

        Stored order is authoritative: after a successful run the strategy that
        actually resolved is promoted to the front, so a set can learn that e.g.
        a CSS selector beats a role lookup on a particular site.
        """
        return list(self.candidates)

    def by_priority(self) -> SelectorSet:
        """Sort candidates by how well each strategy usually survives redesigns."""
        return self.model_copy(
            update={"candidates": sorted(self.candidates, key=lambda s: -s.priority)}
        )

    def with_fallback(self, other: SelectorSet) -> SelectorSet:
        """Merge in extra candidates, keeping the first occurrence of each strategy."""
        seen = {(c.kind, c.value, c.name) for c in self.candidates}
        merged = list(self.candidates)
        merged.extend(
            c for c in other.candidates if (c.kind, c.value, c.name) not in seen
        )
        return self.model_copy(update={"candidates": merged})

    @property
    def robustness(self) -> int:
        """Score of the best available strategy; low scores are worth re-recording."""
        return max((c.priority for c in self.candidates), default=0)

    def __str__(self) -> str:
        return self.description or " | ".join(str(c) for c in self.ordered()[:2])
