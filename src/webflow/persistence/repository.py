"""Repositories: the only place that maps domain models to and from SQL rows."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Select, select

from webflow.domain.checkpoint import CheckpointRequest, HumanAnswer
from webflow.domain.results import ResultSet
from webflow.domain.run import RunState, RunStatus
from webflow.persistence.db import Database
from webflow.persistence.models import AnswerRow, CheckpointRow, ResultRow, RunRow


class RunRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save(self, run: RunState) -> None:
        """Upsert the whole run state."""
        run.updated_at = datetime.now(UTC)
        payload = run.model_dump(mode="json")
        async with self._db.session() as session:
            row = await session.get(RunRow, run.id)
            if row is None:
                row = RunRow(id=run.id, created_at=run.created_at)
                session.add(row)
            row.provider_id = run.provider_id
            row.goal = run.goal
            row.status = run.status.value
            row.mode = run.mode.value
            row.flow_version = run.flow_version
            row.llm_calls = run.llm_calls
            row.error = run.error
            row.state = payload
            row.updated_at = run.updated_at
            row.finished_at = run.finished_at

    async def get(self, run_id: str) -> RunState | None:
        async with self._db.session() as session:
            row = await session.get(RunRow, run_id)
            return RunState.model_validate(row.state) if row else None

    async def list_awaiting_human(self, provider_id: str | None = None) -> list[RunState]:
        stmt = select(RunRow).where(RunRow.status == RunStatus.AWAITING_HUMAN.value)
        if provider_id:
            stmt = stmt.where(RunRow.provider_id == provider_id)
        return await self._fetch(stmt.order_by(RunRow.updated_at.desc()))

    async def list_recent(self, limit: int = 20) -> list[RunState]:
        stmt = select(RunRow).order_by(RunRow.created_at.desc()).limit(limit)
        return await self._fetch(stmt)

    async def _fetch(self, stmt: Select[tuple[RunRow]]) -> list[RunState]:
        async with self._db.session() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [RunState.model_validate(r.state) for r in rows]


class CheckpointRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def open(self, run: RunState, request: CheckpointRequest) -> None:
        async with self._db.session() as session:
            session.add(
                CheckpointRow(
                    id=request.id,
                    run_id=run.id,
                    provider_id=run.provider_id,
                    goal=run.goal,
                    reason=request.reason.value,
                    fingerprint=request.fingerprint,
                    question=request.question,
                    status="pending",
                    request=request.model_dump(mode="json"),
                    created_at=request.created_at,
                )
            )

    async def resolve(self, answer: HumanAnswer) -> None:
        async with self._db.session() as session:
            row = await session.get(CheckpointRow, answer.checkpoint_id)
            if row is None:
                return
            row.status = "aborted" if answer.aborted else "answered"
            row.answer = answer.model_dump(mode="json")
            row.answered_at = answer.answered_at

    async def list_pending(self, provider_id: str | None = None) -> list[CheckpointRequest]:
        stmt = select(CheckpointRow).where(CheckpointRow.status == "pending")
        if provider_id:
            stmt = stmt.where(CheckpointRow.provider_id == provider_id)
        async with self._db.session() as session:
            rows = (await session.execute(stmt.order_by(CheckpointRow.created_at))).scalars().all()
            return [CheckpointRequest.model_validate(r.request) for r in rows]

    async def run_id_for(self, checkpoint_id: str) -> str | None:
        async with self._db.session() as session:
            row = await session.get(CheckpointRow, checkpoint_id)
            return row.run_id if row else None


class AnswerRepository:
    """Backing store for the answer bank."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def lookup(self, fingerprint: str) -> dict[str, str]:
        stmt = select(AnswerRow).where(AnswerRow.fingerprint == fingerprint)
        async with self._db.session() as session:
            rows = (await session.execute(stmt)).scalars().all()
            for row in rows:
                row.use_count += 1
            return {r.field_key: r.value for r in rows}

    async def remember(
        self,
        *,
        provider_id: str,
        fingerprint: str,
        question: str,
        values: dict[str, str],
        profile_keys: dict[str, str | None] | None = None,
    ) -> None:
        profile_keys = profile_keys or {}
        async with self._db.session() as session:
            for key, value in values.items():
                row = await session.get(AnswerRow, (fingerprint, key))
                if row is None:
                    row = AnswerRow(fingerprint=fingerprint, field_key=key)
                    session.add(row)
                row.provider_id = provider_id
                row.value = value
                row.question = question
                row.profile_key = profile_keys.get(key)

    async def forget(self, fingerprint: str) -> None:
        stmt = select(AnswerRow).where(AnswerRow.fingerprint == fingerprint)
        async with self._db.session() as session:
            for row in (await session.execute(stmt)).scalars().all():
                await session.delete(row)


class ResultRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save(self, run_id: str, results: ResultSet) -> None:
        async with self._db.session() as session:
            session.add(
                ResultRow(
                    run_id=run_id,
                    provider_id=results.provider_id,
                    goal=results.goal,
                    payload=results.model_dump(mode="json"),
                )
            )

    async def latest(self, provider_id: str, goal: str) -> ResultSet | None:
        stmt = (
            select(ResultRow)
            .where(ResultRow.provider_id == provider_id, ResultRow.goal == goal)
            .order_by(ResultRow.created_at.desc())
            .limit(1)
        )
        async with self._db.session() as session:
            row = (await session.execute(stmt)).scalars().first()
            return ResultSet.model_validate(row.payload) if row else None
