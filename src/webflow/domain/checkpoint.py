"""Human-in-the-loop checkpoint models.

A checkpoint is a *serialisable question*. The engine never holds a live browser
open while waiting for an answer: it persists the request, tears the browser
down, and rebuilds the page later from the recorded trajectory + storage state.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class CheckpointReason(StrEnum):
    CAPTCHA = "captcha"
    MFA = "mfa"
    LOGIN = "login"
    AMBIGUOUS_QUESTION = "ambiguous_question"
    MISSING_PROFILE_DATA = "missing_profile_data"
    CONSENT = "consent"
    APPROVAL = "approval"
    """Agent is stuck / low confidence and wants a human to look."""
    LOW_CONFIDENCE = "low_confidence"
    GUARD_BLOCKED = "guard_blocked"

    @property
    def needs_live_browser(self) -> bool:
        """Reasons a human must solve in the browser itself, not by typing an answer."""
        return self in {CheckpointReason.CAPTCHA, CheckpointReason.MFA, CheckpointReason.LOGIN}


AnswerFieldType = Literal["text", "number", "date", "boolean", "choice", "multi_choice", "secret"]


class AnswerField(BaseModel):
    """One value the human is being asked for."""

    key: str
    prompt: str
    type: AnswerFieldType = "text"
    choices: list[str] = Field(default_factory=list)
    unit: str | None = None
    example: str | None = None
    required: bool = True
    """Dotted profile path this answer should be persisted to, if it is reusable."""
    profile_key: str | None = None
    """When false the answer is one-shot (e.g. an SMS code) and must not be cached."""
    reusable: bool = True


class CheckpointRequest(BaseModel):
    """What the engine asks a human, plus enough context to answer it offline."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    reason: CheckpointReason
    question: str
    fields: list[AnswerField] = Field(default_factory=list)
    url: str | None = None
    page_title: str | None = None
    """Trimmed page text so a human can answer without opening a browser."""
    page_excerpt: str | None = None
    screenshot_path: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """Stable identity of *this question*, so a cached answer can be reused.

        Deliberately ignores volatile context (url query strings, screenshots).
        """
        basis = "|".join([self.reason.value, self.question.strip().lower()])
        basis += "|" + ",".join(sorted(f.key for f in self.fields))
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


class HumanAnswer(BaseModel):
    """A human's reply to a :class:`CheckpointRequest`."""

    checkpoint_id: str
    values: dict[str, str] = Field(default_factory=dict)
    """Set when the human solved it in a live browser instead of typing values."""
    solved_in_browser: bool = False
    """Set when the human wants the run abandoned."""
    aborted: bool = False
    note: str | None = None
    answered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
