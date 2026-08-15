"""Wiring: one container so the runner does not take fifteen constructor args."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from webflow.config import Settings, get_settings
from webflow.flows.store import FlowStore
from webflow.human.queue import InterventionQueue
from webflow.llm.base import LLMClient
from webflow.llm.registry import create_llm_client
from webflow.persistence.db import Database
from webflow.persistence.repository import (
    AnswerRepository,
    CheckpointRepository,
    ResultRepository,
    RunRepository,
)
from webflow.profiles import ProfileStore


@dataclass
class Services:
    settings: Settings
    database: Database
    runs: RunRepository
    checkpoints: CheckpointRepository
    answers: AnswerRepository
    results: ResultRepository
    queue: InterventionQueue
    profiles: ProfileStore
    llm: LLMClient

    @classmethod
    def create(
        cls,
        *,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        profile_path: Path | None = None,
    ) -> Services:
        settings = settings or get_settings()
        database = Database(settings)
        runs = RunRepository(database)
        checkpoints = CheckpointRepository(database)
        answers = AnswerRepository(database)
        profiles = ProfileStore(profile_path)
        return cls(
            settings=settings,
            database=database,
            runs=runs,
            checkpoints=checkpoints,
            answers=answers,
            results=ResultRepository(database),
            queue=InterventionQueue(runs, checkpoints, answers, profiles),
            profiles=profiles,
            llm=llm or create_llm_client(settings.llm),
        )

    async def aclose(self) -> None:
        await self.database.dispose()


def make_flow_store(provider_flows_dir: Path, settings: Settings | None = None) -> FlowStore:
    """Read local repairs first, but write new versions where they can be committed."""
    settings = settings or get_settings()
    return FlowStore(
        roots=[settings.data_path / "flows", provider_flows_dir],
        write_root=provider_flows_dir,
    )
