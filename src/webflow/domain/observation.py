"""What the agent "sees": a compact, LLM-friendly snapshot of a page.

Raw DOM is far too large and noisy to prompt with. Instead each interactive
element gets a stable index, an accessible name and just enough attributes for
the planner to reason about, plus a pre-built :class:`SelectorSet` so the chosen
element can be addressed robustly later.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from webflow.domain.selectors import ROLE_SELECTABLE, Selector, SelectorKind, SelectorSet


class InteractiveElement(BaseModel):
    """One thing on the page the agent could act on."""

    """Position in this observation only; never persisted into a flow."""
    index: int
    tag: str
    role: str
    name: str = ""
    input_type: str | None = None
    value: str | None = None
    placeholder: str | None = None
    label: str | None = None
    options: list[str] = Field(default_factory=list)
    checked: bool | None = None
    required: bool = False
    disabled: bool = False
    element_id: str | None = None
    name_attr: str | None = None
    test_id: str | None = None
    css: str | None = None
    frame_url: str | None = None
    """Set when the element sits inside a labelled group, e.g. a radio question."""
    group_label: str | None = None

    @property
    def is_input(self) -> bool:
        return self.tag in {"input", "textarea", "select"}

    def to_selector_set(self) -> SelectorSet:
        candidates: list[Selector] = []
        if self.test_id:
            candidates.append(Selector(kind=SelectorKind.TEST_ID, value=self.test_id))
        if self.role in ROLE_SELECTABLE and self.name:
            candidates.append(
                Selector(kind=SelectorKind.ROLE, value=self.role, name=self.name, exact=True)
            )
        if self.label:
            candidates.append(Selector(kind=SelectorKind.LABEL, value=self.label, exact=True))
        if self.placeholder:
            candidates.append(Selector(kind=SelectorKind.PLACEHOLDER, value=self.placeholder))
        if self.element_id:
            candidates.append(Selector(kind=SelectorKind.ELEMENT_ID, value=self.element_id))
        if self.name_attr:
            candidates.append(Selector(kind=SelectorKind.NAME_ATTR, value=self.name_attr))
        if self.name and not self.is_input:
            candidates.append(Selector(kind=SelectorKind.TEXT, value=self.name, exact=True))
        if self.css:
            candidates.append(Selector(kind=SelectorKind.CSS, value=self.css))
        return SelectorSet(
            candidates=candidates,
            description=self.describe(),
            frame_url=self.frame_url,
        )

    def describe(self) -> str:
        label = self.name or self.label or self.placeholder or self.name_attr or self.tag
        prefix = f"{self.group_label} / " if self.group_label else ""
        return f"{prefix}{self.role}:{label}"

    def to_prompt_line(self) -> str:
        """One line of the element list handed to the LLM."""
        parts = [f"[{self.index}]", self.role]
        if self.name:
            parts.append(f'"{self.name}"')
        if self.group_label:
            parts.append(f"(in: {self.group_label})")
        if self.input_type:
            parts.append(f"type={self.input_type}")
        if self.placeholder:
            parts.append(f"placeholder={self.placeholder!r}")
        if self.options:
            shown = ", ".join(self.options[:12])
            suffix = ", ..." if len(self.options) > 12 else ""
            parts.append(f"options=[{shown}{suffix}]")
        if self.value:
            parts.append(f"value={self.value!r}")
        if self.checked is not None:
            parts.append("checked" if self.checked else "unchecked")
        if self.required:
            parts.append("required")
        if self.disabled:
            parts.append("disabled")
        return " ".join(parts)


class PageObservation(BaseModel):
    """A single snapshot of the page state."""

    url: str
    title: str = ""
    elements: list[InteractiveElement] = Field(default_factory=list)
    """Visible page text, trimmed - gives the planner question wording and context."""
    text: str = ""
    """Validation messages detected on the page; the planner uses these to self-correct."""
    validation_messages: list[str] = Field(default_factory=list)
    screenshot_path: str | None = None
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def by_index(self, index: int) -> InteractiveElement | None:
        return next((e for e in self.elements if e.index == index), None)

    def to_prompt(self, element_limit: int = 120, text_limit: int = 3_000) -> str:
        lines = [f"URL: {self.url}", f"TITLE: {self.title}", "", "PAGE TEXT:"]
        lines.append(self.text[:text_limit])
        if self.validation_messages:
            lines += ["", "VALIDATION ERRORS:"]
            lines += [f"- {m}" for m in self.validation_messages]
        lines += ["", "INTERACTIVE ELEMENTS:"]
        lines += [e.to_prompt_line() for e in self.elements[:element_limit]]
        if len(self.elements) > element_limit:
            lines.append(f"... {len(self.elements) - element_limit} more elements not shown")
        return "\n".join(lines)

    def signature(self) -> str:
        """Cheap identity of a page state, used to detect the agent looping."""
        names = "|".join(e.describe() for e in self.elements[:40])
        return f"{self.url}#{hash(names) & 0xFFFFFFFF:08x}"
