"""The planner: one LLM turn -> one executable action."""

from __future__ import annotations

from dataclasses import dataclass, field

from webflow.agent import prompts
from webflow.agent.schema import DemonstrationReview, PlannedAction, PlannerDecision, to_action
from webflow.config import AgentSettings
from webflow.domain.actions import Action
from webflow.domain.observation import PageObservation
from webflow.domain.values import ProfileKeyInfo
from webflow.llm.base import LLMClient, NullLLMClient
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

    async def review_demonstration(
        self,
        context: PlannerContext,
        observation_before: PageObservation,
        actions: list[Action],
        *,
        reason: str = "human took over",
    ) -> DemonstrationReview:
        """Ask which actions of a human take-over are worth keeping in the flow."""
        if isinstance(self._llm, NullLLMClient) or not actions:
            return DemonstrationReview(
                keep_indexes=list(range(len(actions))),
                flow_note=f"human demonstration: {reason}",
            )
        user = prompts.build_demonstration_prompt(
            goal=context.goal,
            reason=reason,
            observation_before=observation_before,
            actions=[f"{a.type} {getattr(a, 'target', '') or ''}".strip() for a in actions],
        )
        review = await self._llm.generate_structured(
            prompts.DEMONSTRATION_SYSTEM_PROMPT, user, DemonstrationReview
        )
        log.info("demonstration_reviewed", kept=len(review.keep_indexes), of=len(actions))
        return review


__all__ = ["PlannedAction", "Planner", "PlannerContext", "PlannerDecision"]
