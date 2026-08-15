"""The planner: one LLM turn -> one executable action."""

from __future__ import annotations

from dataclasses import dataclass, field

from webflow.agent import prompts
from webflow.agent.schema import PlannedAction, PlannerDecision, to_action
from webflow.config import AgentSettings
from webflow.domain.actions import Action
from webflow.domain.observation import PageObservation
from webflow.domain.values import ProfileKeyInfo
from webflow.llm.base import LLMClient
from webflow.logging import get_logger

log = get_logger(__name__)


@dataclass
class PlannerContext:
    """Everything about the task that does not change between turns."""

    goal: str
    goal_description: str
    provider_name: str
    profile_keys: list[ProfileKeyInfo] = field(default_factory=list)
    answers: dict[str, str] = field(default_factory=dict)
    hints: list[str] = field(default_factory=list)


class Planner:
    def __init__(self, llm: LLMClient, settings: AgentSettings | None = None) -> None:
        self._llm = llm
        self._settings = settings or AgentSettings()

    async def next_action(
        self,
        observation: PageObservation,
        context: PlannerContext,
        history: list[str],
    ) -> tuple[Action, PlannerDecision]:
        user = prompts.build_user_prompt(
            goal=context.goal,
            goal_description=context.goal_description,
            provider_name=context.provider_name,
            observation=observation,
            profile_keys=context.profile_keys,
            history=history,
            answers=context.answers,
            element_limit=self._settings.observation_element_limit,
            hints=context.hints,
        )
        decision = await self._llm.generate_structured(
            prompts.SYSTEM_PROMPT, user, PlannerDecision
        )
        log.info(
            "planner_decision",
            kind=decision.action.kind,
            element=decision.action.element_index,
            page=decision.page_summary[:120],
        )
        return to_action(decision.action, observation), decision

    async def repair_step(
        self,
        observation: PageObservation,
        context: PlannerContext,
        *,
        step_index: int,
        step_description: str,
        error: str,
    ) -> Action:
        """Ask for a replacement for one step whose selectors went stale."""
        user = prompts.build_repair_prompt(
            goal=context.goal,
            step_index=step_index,
            step_description=step_description,
            observation=observation,
            profile_keys=context.profile_keys,
            error=error,
        )
        decision = await self._llm.generate_structured(
            prompts.REPAIR_SYSTEM_PROMPT, user, PlannerDecision
        )
        log.info("repair_decision", step=step_index, kind=decision.action.kind)
        return to_action(decision.action, observation)


__all__ = ["PlannedAction", "Planner", "PlannerContext", "PlannerDecision"]
