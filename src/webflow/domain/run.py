"""Run state - the object that makes a run survivable across process restarts.

Everything needed to rebuild a half-finished session lives here: the actions
already executed, the browser storage state, the answers given so far and the
question currently blocking progress.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from webflow.domain.actions import Action
from webflow.domain.checkpoint import CheckpointRequest
from webflow.domain.results import ResultSet


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    """Suspended on a checkpoint. No browser is held open in this state."""
    AWAITING_HUMAN = "awaiting_human"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"

    @property
    def is_terminal(self) -> bool:
        return self in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.ABORTED}


class RunMode(StrEnum):
    """Replaying a cached flow, no LLM involved."""

    REPLAY = "replay"
    """Exploring with the LLM planner because no flow exists."""
    AGENT = "agent"
    """Replaying, but the planner is patching a broken step."""
    REPAIR = "repair"


class StepStatus(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"
    FAILED = "failed"
    REPAIRED = "repaired"


class ExecutedStep(BaseModel):
    """An action that actually ran, kept so the run can be rebuilt on resume."""

    index: int
    action: Action
    status: StepStatus = StepStatus.OK
    mode: RunMode = RunMode.AGENT
    url_before: str | None = None
    url_after: str | None = None
    duration_ms: int = 0
    error: str | None = None
    screenshot_path: str | None = None
    executed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_replayable(self) -> bool:
        """Control actions and failures must not be re-executed when resuming."""
        return self.status in {StepStatus.OK, StepStatus.REPAIRED} and self.action.type not in {
            "extract",
            "human_checkpoint",
            "done",
        }


class RunState(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    provider_id: str
    goal: str
    status: RunStatus = RunStatus.PENDING
    mode: RunMode = RunMode.AGENT
    """Flow version being replayed, or None while exploring."""
    flow_version: int | None = None

    trajectory: list[ExecutedStep] = Field(default_factory=list)
    """Cookies + localStorage, so a resumed run keeps its session."""
    storage_state: dict[str, Any] | None = None
    last_url: str | None = None

    pending_checkpoint: CheckpointRequest | None = None
    """Answers collected during this run, keyed by answer key."""
    answers: dict[str, str] = Field(default_factory=dict)
    """Checkpoints already satisfied, so resume does not ask again."""
    resolved_checkpoints: list[str] = Field(default_factory=list)

    results: ResultSet | None = None
    error: str | None = None
    llm_calls: int = 0

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def replayable_actions(self) -> list[Action]:
        """The prefix to fast-forward through when rebuilding this session."""
        return [s.action for s in self.trajectory if s.is_replayable]

    def record(self, step: ExecutedStep) -> None:
        self.trajectory.append(step)
        self.url_touch(step.url_after)

    def url_touch(self, url: str | None) -> None:
        if url:
            self.last_url = url
        self.updated_at = datetime.now(UTC)

    def suspend(self, request: CheckpointRequest) -> None:
        self.pending_checkpoint = request
        self.status = RunStatus.AWAITING_HUMAN
        self.updated_at = datetime.now(UTC)

    def finish(self, status: RunStatus, error: str | None = None) -> None:
        self.status = status
        self.error = error
        self.finished_at = datetime.now(UTC)
        self.updated_at = self.finished_at
