from __future__ import annotations

import asyncio
import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from requests import RequestException

try:
    from backend.agent_runtime import (
        direct_search_fallback_answer,
        extract_citations,
        get_agent,
        needs_recovery,
        prompt_requires_sources,
        recover_final_answer,
        recover_final_answer_with_sources,
    )
    from backend.store import SqlRunStore
except ModuleNotFoundError:
    from agent_runtime import (
        direct_search_fallback_answer,
        extract_citations,
        get_agent,
        needs_recovery,
        prompt_requires_sources,
        recover_final_answer,
        recover_final_answer_with_sources,
    )
    from store import SqlRunStore


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class RunRecord:
    run_id: str
    prompt: str
    thread_id: str
    status: str = "queued"
    started_at: datetime = field(default_factory=_utc_now)
    completed_at: datetime | None = None
    duration_ms: int | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    final_answer: str | None = None
    citations: list[str] = field(default_factory=list)
    error: str | None = None
    recovery_attempted: bool = False


@dataclass(slots=True)
class _Subscriber:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[dict[str, Any]]


class RunManager:
    def __init__(self, db_url: str | None = None) -> None:
        self._store = SqlRunStore(db_url=db_url) if db_url else SqlRunStore()
        self._subscribers: dict[str, list[_Subscriber]] = {}
        self._subscribers_lock = threading.Lock()

    async def start_run(self, prompt: str, thread_id: str) -> RunRecord:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        state = self._store.create_run(run_id=run_id, prompt=prompt, thread_id=thread_id)

        self._push_event(
            run_id=run_id,
            event_type="run_started",
            actor="system",
            label="Run started",
            payload={"prompt": prompt, "thread_id": thread_id},
        )
        self._store.update_run(run_id, status="running")
        threading.Thread(
            target=self._run_sync,
            args=(run_id,),
            daemon=True,
            name=f"run-worker-{run_id}",
        ).start()

        return RunRecord(
            run_id=state.run_id,
            prompt=state.prompt,
            thread_id=state.thread_id,
            status="running",
            started_at=state.started_at,
        )

    async def get_run(self, run_id: str) -> RunRecord | None:
        state = self._store.get_run(run_id)
        if state is None:
            return None
        events = self._store.list_events(run_id)
        return RunRecord(
            run_id=state.run_id,
            prompt=state.prompt,
            thread_id=state.thread_id,
            status=state.status,
            started_at=state.started_at,
            completed_at=state.completed_at,
            duration_ms=state.duration_ms,
            events=events,
            final_answer=state.final_answer,
            citations=state.citations,
            error=state.error,
            recovery_attempted=state.recovery_attempted,
        )

    async def get_events(self, run_id: str) -> list[dict[str, Any]]:
        return self._store.list_events(run_id)

    async def list_runs(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
        thread_id: str | None = None,
    ) -> tuple[list[RunRecord], int, dict[str, int]]:
        states = self._store.list_runs(
            limit=limit, offset=offset, status=status, thread_id=thread_id
        )
        total = self._store.count_runs(status=status, thread_id=thread_id)
        run_ids = [item.run_id for item in states]
        event_counts = self._store.event_counts(run_ids)
        records = [
            RunRecord(
                run_id=state.run_id,
                prompt=state.prompt,
                thread_id=state.thread_id,
                status=state.status,
                started_at=state.started_at,
                completed_at=state.completed_at,
                duration_ms=state.duration_ms,
                events=[],
                final_answer=state.final_answer,
                citations=state.citations,
                error=state.error,
                recovery_attempted=state.recovery_attempted,
            )
            for state in states
        ]
        return records, total, event_counts

    async def is_done(self, run_id: str) -> bool:
        state = self._store.get_run(run_id)
        if state is None:
            return True
        return state.status in {"completed", "failed"}

    def _run_sync(self, run_id: str) -> None:
        state = self._store.get_run(run_id)
        if state is None:
            return

        prompt = state.prompt
        thread_id = state.thread_id
        recovery_attempted = state.recovery_attempted

        agent = get_agent()
        seen_tool_calls: set[str] = set()
        seen_subagent_messages: set[str] = set()
        last_message_count = 0

        try:
            stream_iter = agent.stream(
                {"messages": [{"role": "user", "content": prompt}]},
                config={"configurable": {"thread_id": thread_id}},
                stream_mode="values",
            )

            self._push_event(
                run_id=run_id,
                event_type="model_started",
                actor="model",
                label="Model started reasoning",
                payload={"model": "deep-agent"},
            )

            latest_messages: list[Any] = []
            for snapshot in stream_iter:
                messages = snapshot.get("messages", [])
                if not isinstance(messages, list):
                    continue

                latest_messages = messages
                if len(messages) < last_message_count:
                    last_message_count = 0
                fresh_messages = messages[last_message_count:]
                last_message_count = len(messages)

                for message in fresh_messages:
                    for tool_call in getattr(message, "tool_calls", []) or []:
                        tool_key = str(
                            tool_call.get("id")
                            or f"{tool_call.get('name')}:{tool_call.get('args')}"
                        )
                        if tool_key in seen_tool_calls:
                            continue
                        seen_tool_calls.add(tool_key)
                        self._push_event(
                            run_id=run_id,
                            event_type="tool_called",
                            actor="tool",
                            label=f"Tool: {tool_call.get('name', 'unknown')}",
                            payload={
                                "name": tool_call.get("name"),
                                "args": tool_call.get("args"),
                            },
                        )

                    subagent_name = getattr(message, "name", None)
                    message_content = str(getattr(message, "content", "") or "").strip()
                    if subagent_name:
                        subagent_key = f"{subagent_name}:{message_content[:200]}"
                        if subagent_key not in seen_subagent_messages:
                            seen_subagent_messages.add(subagent_key)
                            self._push_event(
                                run_id=run_id,
                                event_type="subagent_update",
                                actor=subagent_name,
                                label=f"Subagent: {subagent_name}",
                                payload={
                                    "content_preview": message_content[:240],
                                },
                            )

            final_answer = (
                str(latest_messages[-1].content) if latest_messages else "(No response)"
            )

            source_required = prompt_requires_sources(prompt)
            if needs_recovery(prompt, final_answer):
                recovery_attempted = True
                self._push_event(
                    run_id=run_id,
                    event_type="recovery_attempted",
                    actor="system",
                    label="Recovering final answer",
                    payload={"success": False},
                )
                recovered = recover_final_answer(thread_id)
                if recovered:
                    final_answer = recovered
                    self._push_event(
                        run_id=run_id,
                        event_type="recovery_attempted",
                        actor="system",
                        label="Recovered final answer",
                        payload={"success": True},
                    )

            if source_required and not extract_citations(final_answer):
                recovery_attempted = True
                self._push_event(
                    run_id=run_id,
                    event_type="source_recovery_attempted",
                    actor="system",
                    label="Recovering answer with required source URLs",
                    payload={"success": False},
                )
                recovered_with_sources = recover_final_answer_with_sources(
                    thread_id, prompt
                )
                if recovered_with_sources:
                    final_answer = recovered_with_sources
                    self._push_event(
                        run_id=run_id,
                        event_type="source_recovery_attempted",
                        actor="system",
                        label="Recovered answer with required source URLs",
                        payload={"success": True},
                    )

            if not final_answer.strip():
                if not recovery_attempted:
                    recovery_attempted = True
                    self._push_event(
                        run_id=run_id,
                        event_type="recovery_attempted",
                        actor="system",
                        label="Recovering empty final answer",
                        payload={"success": False},
                    )
                    recovered = recover_final_answer(thread_id)
                    if recovered and recovered.strip():
                        final_answer = recovered
                        self._push_event(
                            run_id=run_id,
                            event_type="recovery_attempted",
                            actor="system",
                            label="Recovered empty final answer",
                            payload={"success": True},
                        )

            if not final_answer.strip():
                fallback = self._last_non_empty_message(latest_messages)
                if fallback:
                    final_answer = fallback
                    self._push_event(
                        run_id=run_id,
                        event_type="final_answer_fallback",
                        actor="system",
                        label="Using fallback answer from prior message",
                        payload={},
                        level="warn",
                    )

            if not final_answer.strip():
                self._push_event(
                    run_id=run_id,
                    event_type="final_answer_missing",
                    actor="system",
                    label="Final answer missing",
                    payload={"reason": "Model returned empty content"},
                    level="error",
                )
                self._fail_run(
                    run_id=run_id,
                    message="Model returned an empty final answer.",
                    recovery_attempted=recovery_attempted,
                )
                return

            if source_required and not extract_citations(final_answer):
                fallback_answer = direct_search_fallback_answer(prompt)
                if fallback_answer and extract_citations(fallback_answer):
                    final_answer = fallback_answer
                    self._push_event(
                        run_id=run_id,
                        event_type="direct_search_fallback",
                        actor="tool",
                        label="Recovered answer via direct internet_search fallback",
                        payload={"success": True},
                    )

            if source_required and not extract_citations(final_answer):
                self._push_event(
                    run_id=run_id,
                    event_type="final_answer_missing_sources",
                    actor="system",
                    label="Final answer missing source URLs",
                    payload={},
                    level="error",
                )
                self._fail_run(
                    run_id=run_id,
                    message="Model returned final answer without source URLs.",
                    recovery_attempted=recovery_attempted,
                )
                return

            completed_at = _utc_now()
            duration_ms = int((completed_at - state.started_at).total_seconds() * 1000)
            citations = extract_citations(final_answer)
            self._store.update_run(
                run_id,
                final_answer=final_answer,
                citations=citations,
                status="completed",
                completed_at=completed_at,
                duration_ms=duration_ms,
                recovery_attempted=recovery_attempted,
            )
            self._push_event(
                run_id=run_id,
                event_type="final_answer",
                actor="assistant",
                label="Final answer generated",
                payload={
                    "answer": final_answer,
                    "citations": citations,
                },
            )
            self._push_event(
                run_id=run_id,
                event_type="run_completed",
                actor="system",
                label="Run completed",
                payload={"duration_ms": duration_ms},
            )
        except RequestException as exc:
            self._fail_run(
                run_id=run_id,
                message=f"Network/API error while calling model: {exc}",
                recovery_attempted=recovery_attempted,
            )
        except Exception as exc:  # noqa: BLE001
            self._fail_run(
                run_id=run_id,
                message=f"Run failed: {exc}",
                recovery_attempted=recovery_attempted,
            )

    @staticmethod
    def _last_non_empty_message(messages: list[Any]) -> str | None:
        for message in reversed(messages):
            msg_name = str(getattr(message, "name", "") or "").lower()
            msg_type = type(message).__name__.lower()
            if msg_type.startswith("human") or msg_name == "user":
                continue
            content = str(getattr(message, "content", "") or "").strip()
            if content:
                return content
        return None

    def _fail_run(self, *, run_id: str, message: str, recovery_attempted: bool) -> None:
        state = self._store.get_run(run_id)
        if state is None:
            return
        completed_at = _utc_now()
        duration_ms = int((completed_at - state.started_at).total_seconds() * 1000)
        self._store.update_run(
            run_id,
            status="failed",
            error=message,
            completed_at=completed_at,
            duration_ms=duration_ms,
            recovery_attempted=recovery_attempted,
        )
        self._push_event(
            run_id=run_id,
            event_type="run_failed",
            actor="system",
            label="Run failed",
            payload={"error": message},
            level="error",
        )

    def _push_event(
        self,
        *,
        run_id: str,
        event_type: str,
        actor: str,
        label: str,
        payload: dict[str, Any] | None = None,
        level: str = "info",
    ) -> None:
        event = {
            "run_id": run_id,
            "timestamp": _iso(_utc_now()),
            "type": event_type,
            "actor": actor,
            "label": label,
            "payload": payload or {},
            "level": level,
        }
        self._store.add_event(event)
        with self._subscribers_lock:
            subscribers = list(self._subscribers.get(run_id, []))

        for subscriber in subscribers:
            try:
                subscriber.loop.call_soon_threadsafe(
                    subscriber.queue.put_nowait, event
                )
            except RuntimeError:
                continue

    async def subscribe(self, run_id: str) -> asyncio.Queue[dict[str, Any]] | None:
        record = await self.get_run(run_id)
        if record is None:
            return None
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        subscriber = _Subscriber(loop=asyncio.get_running_loop(), queue=queue)
        with self._subscribers_lock:
            self._subscribers.setdefault(run_id, []).append(subscriber)
        return queue

    async def unsubscribe(
        self, run_id: str, queue: asyncio.Queue[dict[str, Any]]
    ) -> None:
        with self._subscribers_lock:
            subscribers = self._subscribers.get(run_id, [])
            self._subscribers[run_id] = [sub for sub in subscribers if sub.queue is not queue]


def sse_pack(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
