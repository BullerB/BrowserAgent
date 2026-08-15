"""Where a step's input value comes from at replay time.

Recorded flows must never embed personal data. A step stores *how* to look the
value up (``profile_key``/``answer_key``); the literal is only a recording-time
hint kept for debugging and is redacted when the value is sensitive.
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, model_validator

from webflow.domain.errors import ValueResolutionError

REDACTED = "***"


def lookup_dotted(data: dict[str, Any], dotted_key: str) -> Any | None:
    """Resolve ``person.address.street`` against a nested mapping."""
    current: Any = data
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


class ValueContext(BaseModel):
    """Everything a flow may draw values from during a run."""

    profile: dict[str, Any] = {}
    """Answers previously given by a human, keyed by answer key."""
    answers: dict[str, str] = {}
    """Credentials; kept separate so they are never logged or sent to an LLM."""
    secrets: dict[str, str] = {}


class ValueSource(BaseModel):
    """Declares which of the context buckets a value comes from."""

    profile_key: str | None = None
    answer_key: str | None = None
    secret_key: str | None = None
    literal: str | None = None
    sensitive: bool = False

    @model_validator(mode="after")
    def _at_least_one_source(self) -> Self:
        if not any((self.profile_key, self.answer_key, self.secret_key, self.literal)):
            raise ValueError("ValueSource needs at least one of profile/answer/secret/literal")
        if self.secret_key:
            object.__setattr__(self, "sensitive", True)
        return self

    @classmethod
    def of(cls, literal: str) -> ValueSource:
        return cls(literal=literal)

    def resolve(self, context: ValueContext) -> str:
        """First matching bucket wins; ``literal`` is the last resort."""
        if self.secret_key is not None:
            secret = context.secrets.get(self.secret_key)
            if secret is None:
                raise ValueResolutionError(self.secret_key, "secret")
            return secret
        if self.answer_key is not None:
            answer = context.answers.get(self.answer_key)
            if answer is not None:
                return answer
        if self.profile_key is not None:
            value = lookup_dotted(context.profile, self.profile_key)
            if value is not None:
                return str(value)
        if self.literal is not None:
            return self.literal
        missing = self.profile_key or self.answer_key or "?"
        raise ValueResolutionError(missing, "profile/answer")

    def describe(self) -> str:
        """Loggable description that never leaks a value."""
        if self.secret_key:
            return f"secret:{self.secret_key}"
        if self.profile_key:
            return f"profile:{self.profile_key}"
        if self.answer_key:
            return f"answer:{self.answer_key}"
        return REDACTED if self.sensitive else f"literal:{self.literal!r}"


#: Substrings that mark a profile field as personal data. Values of matching keys
#: are never sent to an LLM - the planner references them by key instead.
SENSITIVE_KEY_HINTS: tuple[str, ...] = (
    "email",
    "mail",
    "phone",
    "telefon",
    "tlf",
    "cpr",
    "ssn",
    "address",
    "adresse",
    "street",
    "vej",
    "name",
    "navn",
    "birth",
    "fodsel",
    "foedsel",
    "password",
    "kode",
    "iban",
    "account",
    "konto",
    "license",
    "registration",
    "nummerplade",
)


def is_sensitive_key(dotted_key: str, extra: tuple[str, ...] = ()) -> bool:
    lowered = dotted_key.lower()
    return any(hint in lowered for hint in (*SENSITIVE_KEY_HINTS, *extra))


class ProfileKeyInfo(BaseModel):
    """One profile field as advertised to the planner."""

    key: str
    kind: str
    """Redacted for personal data, so the LLM can still reason about non-PII values."""
    preview: str


def describe_profile(
    profile: dict[str, Any], extra_sensitive: tuple[str, ...] = ()
) -> list[ProfileKeyInfo]:
    """Flatten a profile into leaf keys, redacting anything personal."""
    infos: list[ProfileKeyInfo] = []

    def walk(node: Any, prefix: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{prefix}.{key}" if prefix else str(key))
        elif isinstance(node, list):
            infos.append(
                ProfileKeyInfo(key=prefix, kind="list", preview=f"{len(node)} items")
            )
        elif node is not None and prefix:
            sensitive = is_sensitive_key(prefix, extra_sensitive)
            infos.append(
                ProfileKeyInfo(
                    key=prefix,
                    kind=type(node).__name__,
                    preview=REDACTED if sensitive else str(node)[:60],
                )
            )

    walk(profile, "")
    return sorted(infos, key=lambda i: i.key)
