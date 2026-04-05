import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, HTTPException
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


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/runs", response_model=CreateRunResponse)
async def create_run(payload: CreateRunRequest) -> CreateRunResponse:
    record = await manager.start_run(
        prompt=payload.prompt.strip(), thread_id=payload.thread_id
    )
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


@app.get("/runs/{run_id}/stream")
async def stream_run(run_id: str) -> StreamingResponse:
    record = await manager.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")

    async def event_generator() -> AsyncIterator[str]:
        with record.mutex:
            historical_events = list(record.events)
        for event in historical_events:
            yield sse_pack(event)

        queue = await manager.subscribe(run_id)
        if queue is None:
            return
        try:
            while True:
                if record.done_event.is_set() and queue.empty():
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
