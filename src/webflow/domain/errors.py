"""Exception hierarchy for the whole engine."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from webflow.domain.checkpoint import CheckpointRequest


class WebflowError(Exception):
    """Base class for everything this package raises."""


class ConfigurationError(WebflowError):
    pass


class ProviderNotFoundError(WebflowError):
    pass


class BrowserError(WebflowError):
    pass


class LocatorResolutionError(BrowserError):
    """No candidate in a SelectorSet matched a unique, usable element."""

    def __init__(self, description: str, tried: int, url: str | None = None) -> None:
        super().__init__(f"Could not resolve {description!r} ({tried} strategies tried) at {url}")
        self.description = description
        self.tried = tried
        self.url = url


class ActionExecutionError(BrowserError):
    def __init__(self, action_type: str, reason: str, url: str | None = None) -> None:
        super().__init__(f"Action {action_type!r} failed at {url}: {reason}")
        self.action_type = action_type
        self.reason = reason
        self.url = url


class FlowPlaybackError(WebflowError):
    def __init__(self, message: str, step_index: int, url: str | None = None) -> None:
        super().__init__(f"Step {step_index} failed: {message} (url={url})")
        self.step_index = step_index
        self.url = url


class LLMError(WebflowError):
    pass


class LLMNotConfiguredError(LLMError, ConfigurationError):
    pass


class AgentError(WebflowError):
    pass


class BudgetExceededError(AgentError):
    pass


class AgentStuckError(AgentError):
    """The planner is looping or repeatedly failing and cannot make progress."""


class GuardViolationError(AgentError):
    """The planner proposed something the safety guards refuse to execute."""


class ExtractionError(WebflowError):
    pass


class ValueResolutionError(WebflowError):
    """A step needs a value that is not in the profile, answer bank or secrets."""

    def __init__(self, key: str, source: str) -> None:
        super().__init__(f"No value for {source} key {key!r}")
        self.key = key
        self.source = source


class HumanInterventionRequired(WebflowError):
    """Control-flow signal: the run must suspend until a human answers.

    Carries the request so the orchestrator can persist it and shut the browser
    down instead of blocking on a live page.
    """

    def __init__(self, request: CheckpointRequest) -> None:
        super().__init__(f"Human intervention required: {request.question}")
        self.request = request
