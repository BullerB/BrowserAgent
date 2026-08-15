"""The user profile: the data forms get filled from.

Kept deliberately loose - ``extra`` absorbs anything a specific provider needs
that the core model does not know about, so a new site never requires a schema
change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from webflow.config import get_settings
from webflow.domain.errors import ConfigurationError
from webflow.domain.values import lookup_dotted
from webflow.logging import get_logger

log = get_logger(__name__)


class Person(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    birth_date: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country: str = "DK"


class Vehicle(BaseModel):
    registration_number: str | None = None
    brand: str | None = None
    model: str | None = None
    first_registration_year: str | None = None
    annual_km: str | None = None
    insured_years: str | None = None
    claims_last_3_years: str | None = None
    deductible: str | None = None


class Home(BaseModel):
    address: str | None = None
    home_type: str | None = None
    ownership: str | None = None
    living_area: str | None = None
    construction_year: str | None = None
    residents: str | None = None


class Profile(BaseModel):
    person: Person = Field(default_factory=Person)
    vehicle: Vehicle = Field(default_factory=Vehicle)
    home: Home = Field(default_factory=Home)
    """Anything provider- or goal-specific, addressed as ``extra.<key>``."""
    extra: dict[str, Any] = Field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Nested mapping used for dotted-key lookups during a run."""
        return self.model_dump(exclude_none=True)

    def get(self, dotted_key: str) -> Any | None:
        return lookup_dotted(self.as_dict(), dotted_key)

    def missing(self, keys: tuple[str, ...]) -> list[str]:
        return [k for k in keys if self.get(k) in (None, "")]

    def with_updates(self, updates: dict[str, str]) -> Profile:
        """Return a copy with dotted keys set - used to persist human answers."""
        data = self.model_dump()
        for dotted, value in updates.items():
            node = data
            parts = dotted.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
        return Profile.model_validate(data)


class ProfileStore:
    """Loads and saves the profile JSON file."""

    def __init__(self, path: Path | None = None) -> None:
        settings = get_settings()
        self.path = path or settings.resolve_path(settings.profile_path)

    def load(self) -> Profile:
        if not self.path.is_file():
            raise ConfigurationError(
                f"No profile at {self.path}. Copy profiles/profile.example.json to "
                f"{self.path.name} and fill it in."
            )
        return Profile.model_validate_json(self.path.read_text(encoding="utf-8"))

    def load_or_empty(self) -> Profile:
        return self.load() if self.path.is_file() else Profile()

    def save(self, profile: Profile) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(profile.model_dump(exclude_none=True), indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )

    def apply_updates(self, updates: dict[str, str]) -> Profile:
        """Fold answers a human gave back into the profile so we never re-ask."""
        if not updates:
            return self.load_or_empty()
        profile = self.load_or_empty().with_updates(updates)
        self.save(profile)
        log.info("profile_updated", keys=sorted(updates))
        return profile
