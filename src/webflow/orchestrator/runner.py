"""End-to-end execution of one provider goal.

Strategy, in order of preference:

1. **Replay** a cached flow - fast, deterministic, no LLM.
2. **Repair** individual steps that no longer resolve, then keep replaying.
3. **Explore** with the planner when there is no flow, or replay broke beyond
   repair. A successful exploration is recorded as a new flow.

At any point the run may hit a question only a human can answer. That does not
block: the run state is persisted, the browser is closed, and the call returns
with ``status == awaiting_human``. :meth:`GoalRunner.resume` picks it up later.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from providers.base import Goal, ProviderPlugin
from webflow.agent.guards import Guards, detect_captcha
from webflow.agent.planner import Planner, PlannerContext
from webflow.agent.policies import RunPolicy
from webflow.browser import locators
from webflow.browser.artifacts import capture_screenshot
from webflow.browser.executor import ActionExecutor
from webflow.browser.observer import observe_stable
from webflow.browser.session import BrowserSession
from webflow.domain.actions import (
    Action,
    DoneAction,
    ExtractAction,
    GotoAction,
    HumanCheckpointAction,
)
from webflow.domain.checkpoint import CheckpointReason, CheckpointRequest
from webflow.domain.errors import (
    ActionExecutionError,
    AgentError,
    FlowPlaybackError,
    HumanInterventionRequired,
    LLMNotConfiguredError,
    LocatorResolutionError,
    WebflowError,
)
from webflow.domain.flow import Flow, FlowStep
from webflow.domain.observation import PageObservation
from webflow.domain.results import ResultSet
from webflow.domain.run import ExecutedStep, RunMode, RunState, RunStatus, StepStatus
from webflow.domain.values import ValueContext, describe_profile
from webflow.extraction import extract
from webflow.flows.player import FlowPlayer
from webflow.flows.recorder import record_flow
from webflow.flows.repair import StepRepairer, apply_repairs
from webflow.flows.store import FlowStore
from webflow.human.resume import rehydrate
from webflow.llm.base import NullLLMClient
from webflow.logging import get_logger
from webflow.orchestrator.services import Services, make_flow_store
from webflow.profiles import Profile

log = get_logger(__name__)


@dataclass
class RunOutcome:
    run: RunState
    results: ResultSet | None = None
    """Set when the run stopped for a human; answer it and call resume()."""
    pending: CheckpointRequest | None = None
    flow_version: int | None = None
    recorded_flow: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def awaiting_human(self) -> bool:
        return self.run.status is RunStatus.AWAITING_HUMAN

    @property
    def succeeded(self) -> bool:
        return self.run.status is RunStatus.COMPLETED


class GoalRunner:
    def __init__(
        self,
        provider: ProviderPlugin,
        goal_name: str,
        services: Services,
        *,
        profile: Profile | None = None,
        secrets: dict[str, str] | None = None,
        headless: bool | None = None,
        use_cached_flow: bool = True,
    ) -> None:
        self.provider = provider
        self.goal: Goal = provider.goal(goal_name)
        self.services = services
        self.profile = profile or services.profiles.load_or_empty()
        self.secrets = secrets or {}
        self.headless = headless
        self.use_cached_flow = use_cached_flow

        self.store: FlowStore = make_flow_store(provider.flows_dir, services.settings)
        self.guards = Guards.for_site(provider.base_url, provider.extra_allowed_domains)
        self.policy = RunPolicy(services.settings.agent)
        self.planner = Planner(services.llm, services.settings.agent)

        self._history: list[str] = []
        self._session: BrowserSession | None = None
        self._executor: ActionExecutor | None = None
        self._results: ResultSet | None = None
        self._warnings: list[str] = []

    # ------------------------------------------------------------------ entry

    async def start(self) -> RunOutcome:
        run = RunState(provider_id=self.provider.id, goal=self.goal.name)
        await self.services.runs.save(run)
        return await self._drive(run, resuming=False)

    async def resume(self, run_id: str) -> RunOutcome:
        run = await self.services.runs.get(run_id)
        if run is None:
            raise WebflowError(f"Unknown run {run_id!r}")
        if run.status.is_terminal:
            return RunOutcome(run=run, results=run.results)
        return await self._drive(run, resuming=True)

    # ------------------------------------------------------------- main loop

    async def _drive(self, run: RunState, *, resuming: bool) -> RunOutcome:
        run.status = RunStatus.RUNNING
        self._history = [
            f"{s.index}. {s.action.type} -> {s.status.value}" for s in run.trajectory
        ]
        self.policy.llm_calls = run.llm_calls

        try:
            await self._open_browser(run, resuming=resuming)

            flow = self.store.latest(self.provider.id, self.goal.name)
            if flow is not None and self.use_cached_flow and not resuming:
                run.flow_version = flow.version
                await self._replay(run, flow)
            else:
                await self._explore(run)

            if self._results is None:
                await self._harvest(run)

            return await self._succeed(run)

        except HumanInterventionRequired as pause:
            return await self._suspend(run, pause.request)
        except (AgentError, FlowPlaybackError, WebflowError) as exc:
            return await self._fail(run, str(exc))
        finally:
            await self._close_browser()

    # ------------------------------------------------------------- lifecycle

    async def _open_browser(self, run: RunState, *, resuming: bool) -> None:
        if resuming and run.trajectory:
            session, result = await rehydrate(
                run,
                self._values(run),
                browser_settings=self.services.settings.browser,
                headless=self.headless,
            )
            self._session = session
            self._warnings.extend(result.warnings)
        else:
            self._session = BrowserSession(
                self.services.settings.browser, headless=self.headless
            )
            await self._session.start()
            await self._session.page.goto(self.goal.start_url)
            await self.provider.prepare(self._session.page)
            run.record(
                ExecutedStep(
                    index=0,
                    action=GotoAction(url=self.goal.start_url),
                    mode=RunMode.AGENT,
                    url_after=self._session.page.url,
                )
            )

        self._executor = ActionExecutor(
            self._session.page, self._values(run), settle_ms=self.services.settings.agent.settle_ms
        )

    async def _close_browser(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
            self._executor = None

    def _values(self, run: RunState) -> ValueContext:
        return ValueContext(
            profile=self.profile.as_dict(), answers=run.answers, secrets=self.secrets
        )

    def _planner_context(self, run: RunState) -> PlannerContext:
        return PlannerContext(
            goal=self.goal.name,
            goal_description=self.goal.description,
            provider_name=self.provider.name,
            profile_keys=describe_profile(self.profile.as_dict()),
            answers=run.answers,
            hints=list(self.goal.hints),
        )

    # ---------------------------------------------------------------- replay

    async def _replay(self, run: RunState, flow: Flow) -> None:
        assert self._executor is not None and self._session is not None
        run.mode = RunMode.REPLAY
        log.info("replaying_flow", flow=flow.slug, steps=len(flow.steps))

        repairer = None
        if not isinstance(self.services.llm, NullLLMClient):
            repairer = StepRepairer(
                self.planner, self._session.page, self._planner_context(run)
            )

        async def on_control(step: FlowStep) -> None:
            if isinstance(step.action, HumanCheckpointAction):
                await self._checkpoint(run, step.action.request)

        player = FlowPlayer(
            self._executor,
            repair=repairer,
            on_control=on_control,
        )

        try:
            report = await player.play(flow)
        except FlowPlaybackError as exc:
            log.warning("replay_failed_falling_back_to_agent", flow=flow.slug, error=str(exc))
            self._warnings.append(f"replay failed at step {exc.step_index}, agent took over")
            await self._explore(run)
            return

        for step in report.executed:
            run.record(step)

        updated = apply_repairs(flow, report.repairs, report.promotions)
        if updated is not None:
            updated = updated.model_copy(
                update={"version": self.store.next_version(self.provider.id, self.goal.name)}
            )
            self.store.save(updated)
            run.flow_version = updated.version
            self._warnings.append(f"flow updated to v{updated.version}")

    # --------------------------------------------------------------- explore

    async def _explore(self, run: RunState) -> None:
        assert self._executor is not None and self._session is not None
        run.mode = RunMode.AGENT
        context = self._planner_context(run)
        page = self._session.page

        while True:
            self.policy.before_step()
            observation = await observe_stable(
                page,
                max_elements=self.services.settings.agent.observation_element_limit,
                settle_ms=self.services.settings.agent.settle_ms,
            )
            run.url_touch(observation.url)

            captcha = detect_captcha(observation)
            if captcha:
                await self._checkpoint(
                    run,
                    CheckpointRequest(
                        reason=CheckpointReason.CAPTCHA,
                        question=(
                            "The site is asking for human verification "
                            f"({captcha}). Solve it in the browser, then resume."
                        ),
                        url=observation.url,
                        page_title=observation.title,
                    ),
                )
                continue

            action, decision = await self.planner.next_action(observation, context, self._history)
            self.policy.record_llm_call()
            run.llm_calls = self.policy.llm_calls

            action = self.guards.check(action, observation)

            if isinstance(action, HumanCheckpointAction):
                await self._checkpoint(run, action.request)
                self._note(f"asked the user: {action.request.question[:80]}")
                continue

            if isinstance(action, ExtractAction):
                run.record(
                    ExecutedStep(
                        index=len(run.trajectory),
                        action=action,
                        mode=RunMode.AGENT,
                        url_after=observation.url,
                    )
                )
                await self._harvest(run)
                return

            if isinstance(action, DoneAction):
                run.record(
                    ExecutedStep(
                        index=len(run.trajectory),
                        action=action,
                        mode=RunMode.AGENT,
                        url_after=observation.url,
                    )
                )
                if not action.success:
                    raise AgentError(action.summary or "agent gave up")
                return

            await self._step(run, action, observation, decision.reasoning)

    async def _step(
        self,
        run: RunState,
        action: Action,
        observation: PageObservation,
        reasoning: str,
    ) -> None:
        assert self._executor is not None
        url_before = observation.url
        try:
            outcome = await self._executor.execute(action)
        except (ActionExecutionError, LocatorResolutionError) as exc:
            run.record(
                ExecutedStep(
                    index=len(run.trajectory),
                    action=action,
                    status=StepStatus.FAILED,
                    mode=RunMode.AGENT,
                    url_before=url_before,
                    url_after=url_before,
                    error=str(exc),
                )
            )
            self._note(f"{action.type} FAILED: {exc}")
            self.policy.record_failure(str(exc))
            return

        if outcome.selector_used is not None:
            target = getattr(action, "target", None)
            if target is not None:
                action = action.model_copy(
                    update={"target": locators.promote(target, outcome.selector_used)}
                )

        run.record(
            ExecutedStep(
                index=len(run.trajectory),
                action=action,
                status=StepStatus.SKIPPED if outcome.skipped else StepStatus.OK,
                mode=RunMode.AGENT,
                url_before=url_before,
                url_after=outcome.url_after,
                duration_ms=outcome.duration_ms,
            )
        )
        self._note(f"{action.type} {getattr(action, 'target', '') or ''} -> ok ({reasoning[:60]})")
        self.policy.record_success(observation, action)

    def _note(self, text: str) -> None:
        self._history.append(f"{len(self._history) + 1}. {text}")

    # ------------------------------------------------------------ checkpoints

    async def _checkpoint(self, run: RunState, request: CheckpointRequest) -> None:
        """Satisfy a checkpoint from history, or suspend the run.

        Raising :class:`HumanInterventionRequired` unwinds all the way out of
        the loop so the browser can be closed before we start waiting.
        """
        if request.fingerprint in run.resolved_checkpoints:
            log.debug("checkpoint_already_resolved", question=request.question[:60])
            return

        auto = await self.services.queue.bank.try_answer(request)
        if auto is not None:
            run.answers.update(auto.values)
            run.resolved_checkpoints.append(request.fingerprint)
            self._refresh_values(run)
            self._record_checkpoint(run, request)
            self._note(f"reused a stored answer for: {request.question[:60]}")
            return

        if self._session is not None:
            request.screenshot_path = await capture_screenshot(
                self._session.page, run.id, f"checkpoint_{request.reason.value}"
            )
            run.storage_state = await self._session.export_storage_state()
            run.last_url = self._session.page.url

        # Recorded before suspending so the learned flow knows where to ask next
        # time - and so fast-forward skips it, since checkpoints are not replayable.
        self._record_checkpoint(run, request)
        raise HumanInterventionRequired(request)

    def _record_checkpoint(self, run: RunState, request: CheckpointRequest) -> None:
        if run.mode is not RunMode.AGENT:
            return  # replay already records its own control steps
        run.record(
            ExecutedStep(
                index=len(run.trajectory),
                action=HumanCheckpointAction(request=request),
                mode=RunMode.AGENT,
                url_after=run.last_url,
            )
        )

    def _refresh_values(self, run: RunState) -> None:
        if self._executor is not None:
            self._executor.values = self._values(run)

    # -------------------------------------------------------------- results

    async def _harvest(self, run: RunState) -> None:
        assert self._session is not None
        page = self._session.page
        await self.provider.before_extract(page)

        llm = None if isinstance(self.services.llm, NullLLMClient) else self.services.llm
        try:
            self._results = await extract(
                page,
                provider_id=self.provider.id,
                goal=self.goal.name,
                schema=self.goal.result_schema,
                llm=llm,
            )
        except LLMNotConfiguredError:
            self._results = await extract(
                page, provider_id=self.provider.id, goal=self.goal.name
            )

        if self._results.is_empty:
            self._warnings.append("no records were found on the results page")
            await capture_screenshot(page, run.id, "empty_results")

    # ----------------------------------------------------------- terminating

    async def _succeed(self, run: RunState) -> RunOutcome:
        recorded = False
        if run.mode is RunMode.AGENT and self._results is not None and not self._results.is_empty:
            flow = record_flow(
                run,
                start_url=self.goal.start_url,
                version=self.store.next_version(self.provider.id, self.goal.name),
                result_schema=self.goal.result_schema.name,
            )
            self.store.save(flow)
            run.flow_version = flow.version
            recorded = True

        run.results = self._results
        run.finish(RunStatus.COMPLETED)
        await self.services.runs.save(run)
        if self._results is not None:
            await self.services.results.save(run.id, self._results)

        log.info(
            "run_completed",
            run_id=run.id,
            provider=run.provider_id,
            goal=run.goal,
            records=len(self._results.records) if self._results else 0,
            llm_calls=run.llm_calls,
        )
        return RunOutcome(
            run=run,
            results=self._results,
            flow_version=run.flow_version,
            recorded_flow=recorded,
            warnings=self._warnings,
        )

    async def _suspend(self, run: RunState, request: CheckpointRequest) -> RunOutcome:
        await self.services.queue.suspend(run, request)
        return RunOutcome(
            run=run, pending=request, flow_version=run.flow_version, warnings=self._warnings
        )

    async def _fail(self, run: RunState, error: str) -> RunOutcome:
        if self._session is not None:
            await capture_screenshot(self._session.page, run.id, "failure")
            run.last_url = self._session.page.url
        run.finish(RunStatus.FAILED, error)
        await self.services.runs.save(run)
        log.error("run_failed", run_id=run.id, error=error)
        return RunOutcome(run=run, warnings=[*self._warnings, error])
