import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from backend.run_manager import RunManager, sse_pack
except ModuleNotFoundError:
    from run_manager import RunManager, sse_pack

app = FastAPI(title="Deep Agents UI Backend", version="0.1.0")
manager = RunManager()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateRunRequest(BaseModel):
    prompt: str = Field(min_length=1)
    thread_id: str = Field(default="demo-thread", min_length=1)


class CreateRunResponse(BaseModel):
    run_id: str
    status: str


class CancelRunResponse(BaseModel):
    run_id: str
    status: str
    cancel_requested: bool
    detail: str


class RunSummary(BaseModel):
    run_id: str
    status: str
    prompt: str
    thread_id: str
    started_at: str
    completed_at: str | None
    duration_ms: int | None
    final_answer: str | None
    citations: list[str]
    error: str | None
    recovery_attempted: bool
    event_count: int


class RunListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[RunSummary]


class PruneEventsResponse(BaseModel):
    deleted_events: int
    older_than_days: int
    cutoff_timestamp: str


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/maintenance/prune-events", response_model=PruneEventsResponse)
async def prune_events(
    older_than_days: int = Query(default=30, ge=1, le=3650),
) -> PruneEventsResponse:
    deleted_count, cutoff = await manager.prune_events(older_than_days=older_than_days)
    return PruneEventsResponse(
        deleted_events=deleted_count,
        older_than_days=older_than_days,
        cutoff_timestamp=cutoff.isoformat(),
    )


@app.post("/runs", response_model=CreateRunResponse)
async def create_run(payload: CreateRunRequest) -> CreateRunResponse:
    record = await manager.start_run(
        prompt=payload.prompt.strip(), thread_id=payload.thread_id
    )
    return CreateRunResponse(run_id=record.run_id, status=record.status)


@app.post("/runs/{run_id}/cancel", response_model=CancelRunResponse)
async def cancel_run(run_id: str) -> CancelRunResponse:
    record, cancel_requested, detail = await manager.cancel_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=detail)
    if not cancel_requested:
        raise HTTPException(status_code=409, detail=detail)
    return CancelRunResponse(
        run_id=record.run_id,
        status=record.status,
        cancel_requested=cancel_requested,
        detail=detail,
    )


@app.post("/runs/{run_id}/retry", response_model=CreateRunResponse)
async def retry_run(run_id: str) -> CreateRunResponse:
    record, detail = await manager.retry_run(run_id)
    if record is None:
        if detail == "Run not found":
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=409, detail=detail)
    return CreateRunResponse(run_id=record.run_id, status=record.status)


@app.get("/runs/{run_id}", response_model=RunSummary)
async def get_run(run_id: str) -> RunSummary:
    record = await manager.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunSummary(
        run_id=record.run_id,
        status=record.status,
        prompt=record.prompt,
        thread_id=record.thread_id,
        started_at=record.started_at.isoformat(),
        completed_at=record.completed_at.isoformat() if record.completed_at else None,
        duration_ms=record.duration_ms,
        final_answer=record.final_answer,
        citations=record.citations,
        error=record.error,
        recovery_attempted=record.recovery_attempted,
        event_count=len(record.events),
    )


@app.get("/runs", response_model=RunListResponse)
async def list_runs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: Literal["queued", "running", "completed", "failed", "canceled"] | None = None,
    thread_id: str | None = None,
) -> RunListResponse:
    records, total, event_counts = await manager.list_runs(
        limit=limit, offset=offset, status=status, thread_id=thread_id
    )
    items = [
        RunSummary(
            run_id=record.run_id,
            status=record.status,
            prompt=record.prompt,
            thread_id=record.thread_id,
            started_at=record.started_at.isoformat(),
            completed_at=record.completed_at.isoformat() if record.completed_at else None,
            duration_ms=record.duration_ms,
            final_answer=record.final_answer,
            citations=record.citations,
            error=record.error,
            recovery_attempted=record.recovery_attempted,
            event_count=event_counts.get(record.run_id, 0),
        )
        for record in records
    ]
    return RunListResponse(total=total, limit=limit, offset=offset, items=items)


@app.get("/runs/{run_id}/stream")
async def stream_run(run_id: str) -> StreamingResponse:
    record = await manager.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")

    async def event_generator() -> AsyncIterator[str]:
        historical_events = await manager.get_events(run_id)
        for event in historical_events:
            yield sse_pack(event)

        queue = await manager.subscribe(run_id)
        if queue is None:
            return
        try:
            while True:
                if await manager.is_done(run_id) and queue.empty():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield sse_pack(event)
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            await manager.unsubscribe(run_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
