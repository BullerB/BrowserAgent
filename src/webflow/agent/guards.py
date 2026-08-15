"""Safety guards applied to every action before it touches the browser.

Two concerns:
* the agent must stay on the site it was pointed at;
* it must never commit the user to something irreversible (buy, order, sign,
  delete). Those are converted into a human approval checkpoint instead of being
  executed, so an unattended run stops rather than doing damage.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from webflow.domain.actions import (
    Action,
    GotoAction,
    HumanCheckpointAction,
)
from webflow.domain.checkpoint import CheckpointReason, CheckpointRequest
from webflow.domain.errors import GuardViolationError
from webflow.domain.observation import PageObservation
from webflow.logging import get_logger

log = get_logger(__name__)

#: Substrings on a clickable element that suggest an irreversible commitment.
#: Danish first, since that is the initial target market, then English.
IRREVERSIBLE_HINTS: tuple[str, ...] = (
    "køb",
    "kob",
    "betal",
    "bestil nu",
    "opret police",
    "tegn forsikring",
    "accepter tilbud",
    "underskriv",
    "signer",
    "slet konto",
    "buy",
    "pay now",
    "checkout",
    "place order",
    "confirm purchase",
    "subscribe",
    "sign contract",
    "delete account",
)

#: Phrases that mean "you are being asked to prove you are human".
CAPTCHA_HINTS: tuple[str, ...] = (
    "recaptcha",
    "hcaptcha",
    "cloudflare",
    "i'm not a robot",
    "jeg er ikke en robot",
    "bekræft at du er et menneske",
    "verify you are human",
)


def registrable_domain(host: str) -> str:
    """Good-enough eTLD+1 for allowlisting, without a public-suffix dependency."""
    parts = host.lower().lstrip(".").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()


@dataclass(slots=True)
class Guards:
    allowed_domains: frozenset[str]
    """When true, irreversible-looking clicks become approval checkpoints."""
    require_approval_for_irreversible: bool = True

    @classmethod
    def for_site(cls, base_url: str, extra_domains: tuple[str, ...] = ()) -> Guards:
        host = urlparse(base_url).hostname or ""
        domains = {registrable_domain(host)} | {registrable_domain(d) for d in extra_domains}
        return cls(allowed_domains=frozenset(d for d in domains if d))

    def allows_url(self, url: str) -> bool:
        host = urlparse(url).hostname
        if not host:
            return False
        return registrable_domain(host) in self.allowed_domains

    def check(self, action: Action, observation: PageObservation) -> Action:
        """Return the action to actually run - possibly a checkpoint instead."""
        if isinstance(action, GotoAction) and not self.allows_url(action.url):
            raise GuardViolationError(
                f"Refusing to navigate outside {sorted(self.allowed_domains)}: {action.url}"
            )

        if self.require_approval_for_irreversible:
            label = self._irreversible_label(action)
            if label is not None:
                log.warning("guard_irreversible", action=action.type, label=label)
                return self._approval_checkpoint(label, observation)

        return action

    def _irreversible_label(self, action: Action) -> str | None:
        target = getattr(action, "target", None)
        if target is None:
            return None
        text = str(target).lower()
        return next((hint for hint in IRREVERSIBLE_HINTS if hint in text), None)

    def _approval_checkpoint(self, label: str, observation: PageObservation) -> Action:
        return HumanCheckpointAction(
            request=CheckpointRequest(
                reason=CheckpointReason.APPROVAL,
                question=(
                    f'The agent wants to activate "{label}", which looks irreversible. '
                    "Approve it explicitly to continue."
                ),
                url=observation.url,
                page_title=observation.title,
                page_excerpt=observation.text[:1_000],
                metadata={"blocked_label": label},
            ),
            reasoning="blocked by safety guard",
        )


def detect_captcha(observation: PageObservation) -> str | None:
    """Spot a human-verification wall so the run can suspend instead of failing."""
    haystack = f"{observation.text}\n{' '.join(e.describe() for e in observation.elements)}".lower()
    return next((hint for hint in CAPTCHA_HINTS if hint in haystack), None)


def redact(text: str, secrets: dict[str, str]) -> str:
    """Strip any secret value that leaked into page text before prompting."""
    for value in secrets.values():
        if value and len(value) > 3:
            text = text.replace(value, "***")
    return text
