"""Prompt construction for the planner."""

from __future__ import annotations

from webflow.domain.observation import PageObservation
from webflow.domain.values import ProfileKeyInfo

SYSTEM_PROMPT = """\
You are a careful web-automation planner. You drive a real browser through a
multi-step form on someone's behalf, one action at a time, until a stated goal is
reached.

You are given, each turn: the goal, the current page (URL, visible text,
validation errors and a numbered list of interactive elements), the profile keys
available to fill fields with, and what you already did.

Return exactly ONE next action.

RULES
1. Reference elements only by the [index] shown in INTERACTIVE ELEMENTS of the
   CURRENT page. Indexes change every turn - never reuse an old one.
2. To enter data, prefer `profile_key`. The actual value is filled in locally and
   is deliberately hidden from you; `preview` shows "***" for personal data.
   Use `literal_value` only for page-specific choices that are not personal data
   (for example picking "Volkswagen" in a car-brand dropdown).
    Every `fill`, `fill_and_pick` and `select` action MUST include one of
    `profile_key`, `answer_key` or `literal_value`; never return an empty value
    action. For address autocomplete, use `fill_and_pick` with `person.address`
    when that key is available.
3. If a required field has no suitable profile key and no safe default, use
   `ask_human` with reason `missing_profile_data` and one entry in `fields`
    describing exactly what you need. Set the field's `profile_key` to its dotted
    profile path and use that same path as `key` when the answer is reusable.
    Do not invent personal data. Never guess a name, address, e-mail, phone
    number, birth date or registration number.
4. Use `ask_human` with reason `captcha`, `mfa` or `login` the moment you see one
   - you cannot solve those. Use `consent` or `approval` before anything that
   costs money, sends an application or is otherwise irreversible.
5. Cookie/consent banners: accept them so the form becomes usable.
6. If VALIDATION ERRORS are present, fix the field they refer to instead of
   pressing the continue button again.
7. Prefer `fill_and_pick` for address or search fields that show a suggestion
   list - typing alone often leaves them empty.
8. When the page finally shows the information the goal asked for, return
   `extract`. Return `done` only if the goal cannot be reached at all.
9. Do not navigate away from the site you were given, and do not open unrelated
   pages.

Answer with JSON only.\
"""


def render_profile_keys(keys: list[ProfileKeyInfo]) -> str:
    if not keys:
        return "(none)"
    return "\n".join(f"- {k.key} ({k.kind}) = {k.preview}" for k in keys)


def render_history(entries: list[str], limit: int = 12) -> str:
    if not entries:
        return "(nothing yet - this is the first step)"
    recent = entries[-limit:]
    omitted = len(entries) - len(recent)
    prefix = f"... {omitted} earlier steps omitted\n" if omitted else ""
    return prefix + "\n".join(recent)


def build_user_prompt(
    *,
    goal: str,
    goal_description: str,
    provider_name: str,
    observation: PageObservation,
    profile_keys: list[ProfileKeyInfo],
    history: list[str],
    answers: dict[str, str],
    element_limit: int = 120,
    hints: list[str] | None = None,
) -> str:
    sections = [
        f"GOAL: {goal} - {goal_description}",
        f"SITE: {provider_name}",
    ]
    if hints:
        sections.append("SITE HINTS:\n" + "\n".join(f"- {h}" for h in hints))
    sections += [
        "AVAILABLE PROFILE KEYS:\n" + render_profile_keys(profile_keys),
        "ANSWERS ALREADY GIVEN BY THE USER (reference with answer_key):\n"
        + ("\n".join(f"- {k}" for k in answers) if answers else "(none)"),
        "WHAT YOU HAVE DONE SO FAR:\n" + render_history(history),
        "CURRENT PAGE:\n" + observation.to_prompt(element_limit=element_limit),
    ]
    return "\n\n".join(sections)


REPAIR_SYSTEM_PROMPT = """\
You are repairing one broken step of a previously recorded browser automation.

The step below used to work but its target element can no longer be found,
because the site changed. Look at the current page and pick the element that now
serves the same purpose, returning a single replacement action of the same kind.

If no element on this page serves that purpose, return `ask_human` with reason
`low_confidence`. Answer with JSON only.\
"""


def build_repair_prompt(
    *,
    goal: str,
    step_index: int,
    step_description: str,
    observation: PageObservation,
    profile_keys: list[ProfileKeyInfo],
    error: str,
) -> str:
    return "\n\n".join(
        [
            f"GOAL: {goal}",
            f"BROKEN STEP {step_index}: {step_description}",
            f"FAILURE: {error}",
            "AVAILABLE PROFILE KEYS:\n" + render_profile_keys(profile_keys),
            "CURRENT PAGE:\n" + observation.to_prompt(),
        ]
    )
