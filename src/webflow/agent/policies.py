"""Stop conditions: budgets, loop detection and repeated-failure detection.

Without these an LLM agent will happily click "Videre" forever on a page it
cannot satisfy, burning tokens and leaving no useful trace.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from webflow.config import AgentSettings
from webflow.domain.actions import Action
from webflow.domain.errors import AgentStuckError, BudgetExceededError
from webflow.domain.observation import PageObservation


def action_fingerprint(action: Action) -> str:
    target = getattr(action, "target", None)
    value = getattr(action, "value", None)
    parts = [action.type, str(target) if target else "-"]
    if value is not None:
        parts.append(value.describe())
    return "|".join(parts)


@dataclass
class RunPolicy:
    """Tracks progress across a single agent run."""

    settings: AgentSettings = field(default_factory=AgentSettings)
    steps: int = 0
    llm_calls: int = 0
    consecutive_failures: int = 0
    _recent: deque[str] = field(default_factory=lambda: deque(maxlen=12), init=False)

    def before_step(self) -> None:
        if self.steps >= self.settings.max_steps:
            raise BudgetExceededError(f"Step budget exhausted ({self.settings.max_steps} steps)")
        if self.llm_calls >= self.settings.max_llm_calls:
            raise BudgetExceededError(
                f"LLM call budget exhausted ({self.settings.max_llm_calls} calls)"
            )

    def record_llm_call(self) -> None:
        self.llm_calls += 1

    def record_success(self, observation: PageObservation, action: Action) -> None:
        self.steps += 1
        self.consecutive_failures = 0
        self._check_loop(observation, action)

    def record_failure(self, error: str) -> None:
        self.steps += 1
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.settings.max_consecutive_failures:
            raise AgentStuckError(
                f"{self.consecutive_failures} consecutive failures, last: {error}"
            )

    def _check_loop(self, observation: PageObservation, action: Action) -> None:
        """Same action on an unchanged page means the agent is going in circles."""
        key = f"{observation.signature()}::{action_fingerprint(action)}"
        self._recent.append(key)
        repeats = sum(1 for k in self._recent if k == key)
        if repeats >= self.settings.loop_window:
            raise AgentStuckError(
                f"Repeated the same action {repeats} times without changing the page: {key}"
            )
