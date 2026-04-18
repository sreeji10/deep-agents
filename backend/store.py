from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    Index,
    String,
    Text,
    create_engine,
    func,
    select,
    delete,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DEFAULT_DB_URL = os.getenv("RUN_DB_URL", "sqlite:///./.data/runs.db")


class Base(DeclarativeBase):
    pass


class RunRow(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    final_answer: Mapped[str | None] = mapped_column(Text)
    citations_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    error: Mapped[str | None] = mapped_column(Text)
    recovery_attempted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )


class RunEventRow(Base):
    __tablename__ = "run_events"
    __table_args__ = (Index("ix_run_events_run_id_id", "run_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="info")


@dataclass(slots=True)
class RunState:
    run_id: str
    prompt: str
    thread_id: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None
    final_answer: str | None
    citations: list[str]
    error: str | None
    recovery_attempted: bool


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SqlRunStore:
    def __init__(self, db_url: str = DEFAULT_DB_URL) -> None:
        self._db_url = db_url
        if db_url.startswith("sqlite:///./"):
            db_path = Path(db_url.removeprefix("sqlite:///./"))
            db_path.parent.mkdir(parents=True, exist_ok=True)

        connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
        self._engine = create_engine(db_url, connect_args=connect_args, future=True)
        self._session_factory = sessionmaker(self._engine, expire_on_commit=False)
        Base.metadata.create_all(self._engine)

    def create_run(self, *, run_id: str, prompt: str, thread_id: str) -> RunState:
        started_at = _utc_now()
        row = RunRow(
            run_id=run_id,
            prompt=prompt,
            thread_id=thread_id,
            status="queued",
            started_at=started_at,
            completed_at=None,
            duration_ms=None,
            final_answer=None,
            citations_json="[]",
            error=None,
            recovery_attempted=False,
        )
        with self._session_factory() as session:
            session.add(row)
            session.commit()
        return self.get_run(run_id)  # type: ignore[return-value]

    def get_run(self, run_id: str) -> RunState | None:
        with self._session_factory() as session:
            row = session.get(RunRow, run_id)
            if row is None:
                return None
            return self._to_state(row)

    def update_run(self, run_id: str, **fields: Any) -> RunState | None:
        with self._session_factory() as session:
            row = session.get(RunRow, run_id)
            if row is None:
                return None

            for key, value in fields.items():
                if key == "citations":
                    setattr(row, "citations_json", json.dumps(value or []))
                else:
                    setattr(row, key, value)
            session.commit()
            session.refresh(row)
            return self._to_state(row)

    def add_event(self, event: dict[str, Any]) -> None:
        timestamp = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
        payload = event.get("payload", {})
        row = RunEventRow(
            run_id=event["run_id"],
            timestamp=timestamp,
            type=event["type"],
            actor=event["actor"],
            label=event["label"],
            payload_json=json.dumps(payload),
            level=event.get("level", "info"),
        )
        with self._session_factory() as session:
            session.add(row)
            session.commit()

    def list_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            stmt = select(RunEventRow).where(RunEventRow.run_id == run_id).order_by(
                RunEventRow.id.asc()
            )
            rows = session.scalars(stmt).all()
            return [self._event_to_dict(row) for row in rows]

    def list_runs(
        self,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
        thread_id: str | None = None,
    ) -> list[RunState]:
        with self._session_factory() as session:
            stmt = select(RunRow)
            if status:
                stmt = stmt.where(RunRow.status == status)
            if thread_id:
                stmt = stmt.where(RunRow.thread_id == thread_id)
            stmt = stmt.order_by(RunRow.started_at.desc()).limit(limit).offset(offset)
            rows = session.scalars(stmt).all()
            return [self._to_state(row) for row in rows]

    def count_runs(self, *, status: str | None = None, thread_id: str | None = None) -> int:
        with self._session_factory() as session:
            stmt = select(func.count()).select_from(RunRow)
            if status:
                stmt = stmt.where(RunRow.status == status)
            if thread_id:
                stmt = stmt.where(RunRow.thread_id == thread_id)
            value = session.execute(stmt).scalar_one()
            return int(value)

    def event_counts(self, run_ids: list[str]) -> dict[str, int]:
        if not run_ids:
            return {}
        with self._session_factory() as session:
            stmt = (
                select(RunEventRow.run_id, func.count(RunEventRow.id))
                .where(RunEventRow.run_id.in_(run_ids))
                .group_by(RunEventRow.run_id)
            )
            rows = session.execute(stmt).all()
            return {str(run_id): int(count) for run_id, count in rows}

    def event_count(self, run_id: str) -> int:
        with self._session_factory() as session:
            stmt = select(RunEventRow.id).where(RunEventRow.run_id == run_id)
            return len(session.scalars(stmt).all())

    def prune_events_older_than(self, *, older_than_days: int) -> tuple[int, datetime]:
        safe_days = max(1, older_than_days)
        cutoff = _utc_now() - timedelta(days=safe_days)
        with self._session_factory() as session:
            stmt = delete(RunEventRow).where(RunEventRow.timestamp < cutoff)
            result = session.execute(stmt)
            session.commit()
            deleted = int(result.rowcount or 0)
        return deleted, cutoff

    @staticmethod
    def _to_state(row: RunRow) -> RunState:
        citations = json.loads(row.citations_json or "[]")
        if not isinstance(citations, list):
            citations = []
        return RunState(
            run_id=row.run_id,
            prompt=row.prompt,
            thread_id=row.thread_id,
            status=row.status,
            started_at=_normalize_dt(row.started_at) or _utc_now(),
            completed_at=_normalize_dt(row.completed_at),
            duration_ms=row.duration_ms,
            final_answer=row.final_answer,
            citations=[str(item) for item in citations],
            error=row.error,
            recovery_attempted=row.recovery_attempted,
        )

    @staticmethod
    def _event_to_dict(row: RunEventRow) -> dict[str, Any]:
        payload = json.loads(row.payload_json or "{}")
        if not isinstance(payload, dict):
            payload = {}
        timestamp = _normalize_dt(row.timestamp) or _utc_now()
        return {
            "run_id": row.run_id,
            "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
            "type": row.type,
            "actor": row.actor,
            "label": row.label,
            "payload": payload,
            "level": row.level,
        }
