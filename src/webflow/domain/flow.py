"""Cached flows: the deterministic recipe learned from a successful agent run."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from webflow.domain.actions import Action
from webflow.domain.selectors import SelectorSet

FLOW_SCHEMA_VERSION = 1


class FlowStep(BaseModel):
    index: int
    action: Action
    note: str | None = None
    """Incremented every time self-healing had to rewrite this step's selectors."""
    repair_count: int = 0

    @property
    def target(self) -> SelectorSet | None:
        return getattr(self.action, "target", None)


class Flow(BaseModel):
    """A replayable recipe for one goal on one provider.

    Contains no personal data: every value is a :class:`ValueSource` pointing at
    the profile or answer bank, so flows are safe to commit and share.
    """

    schema_version: int = FLOW_SCHEMA_VERSION
    provider_id: str
    goal: str
    version: int = 1
    start_url: str
    steps: list[FlowStep] = Field(default_factory=list)
    result_schema: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    """Free-form provenance, e.g. "recorded by agent run 3f2a" or "repaired step 12"."""
    notes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def slug(self) -> str:
        return f"{self.provider_id}/{self.goal}/v{self.version}"

    @property
    def robustness(self) -> int:
        """Worst selector score in the flow; the likeliest thing to break first."""
        scores = [s.target.robustness for s in self.steps if s.target is not None]
        return min(scores, default=0)

    def required_profile_keys(self) -> set[str]:
        keys: set[str] = set()
        for step in self.steps:
            source = getattr(step.action, "value", None) or getattr(step.action, "path", None)
            if source is not None and getattr(source, "profile_key", None):
                keys.add(source.profile_key)
        return keys

    @classmethod
    def from_actions(
        cls,
        provider_id: str,
        goal: str,
        start_url: str,
        actions: list[Action],
        *,
        version: int = 1,
        result_schema: str | None = None,
    ) -> Flow:
        return cls(
            provider_id=provider_id,
            goal=goal,
            version=version,
            start_url=start_url,
            result_schema=result_schema,
            steps=[
                FlowStep(index=i, action=a, note=getattr(a, "reasoning", None))
                for i, a in enumerate(actions)
            ],
        )

    def with_replaced_step(self, index: int, action: Action, note: str | None = None) -> Flow:
        """Return a new version of this flow with one step rewritten."""
        steps = [s.model_copy(deep=True) for s in self.steps]
        steps[index] = FlowStep(
            index=index,
            action=action,
            note=note or steps[index].note,
            repair_count=steps[index].repair_count + 1,
        )
        return self.model_copy(
            update={
                "steps": steps,
                "version": self.version + 1,
                "updated_at": datetime.now(UTC),
                "notes": [*self.notes, note or f"repaired step {index}"],
            }
        )

    def reindexed(self) -> Flow:
        steps = [s.model_copy(update={"index": i}) for i, s in enumerate(self.steps)]
        return self.model_copy(update={"steps": steps})
