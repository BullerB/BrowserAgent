"""Data structures shared by the recorder, player and extractor."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

FLOWS_DIR = Path(__file__).parent.parent / "flows"
FLOWS_DIR.mkdir(exist_ok=True)


@dataclass
class Step:
    """One recorded browser action, replayable against fresh profile data."""

    action: str  # goto | click | fill | select | check | press | fill_and_pick
    selector: Optional[str] = None
    text: Optional[str] = None  # visible text, used as fallback locator for click/select
    profile_key: Optional[str] = None  # dotted path into the profile dict, e.g. "person.email"
    value: Optional[str] = None  # literal value, used if profile_key is absent/missing
    url: Optional[str] = None  # for action == "goto"
    key: Optional[str] = None  # for action == "press", e.g. "ArrowDown" or "Enter"

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None} | {"action": self.action}

    @staticmethod
    def from_dict(d: dict) -> "Step":
        return Step(**d)


@dataclass
class Flow:
    """A learned, cached sequence of steps for one insurance product."""

    product: str
    steps: list[Step] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return FLOWS_DIR / f"{self.product}.json"

    def save(self) -> None:
        self.path.write_text(
            json.dumps({"product": self.product, "steps": [s.to_dict() for s in self.steps]}, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def load(product: str) -> Optional["Flow"]:
        path = FLOWS_DIR / f"{product}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return Flow(product=data["product"], steps=[Step.from_dict(s) for s in data["steps"]])


def resolve_value(profile: dict[str, Any], profile_key: Optional[str], literal: Optional[str]) -> Optional[str]:
    """Look up a dotted key in the profile, falling back to the literal value recorded at capture time."""
    if profile_key:
        node: Any = profile
        for part in profile_key.split("."):
            if not isinstance(node, dict) or part not in node:
                node = None
                break
            node = node[part]
        if node is not None:
            return str(node)
    return literal
