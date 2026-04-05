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
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)
    final_answer: str | None = None
    citations: list[str] = field(default_factory=list)
    error: str | None = None
    recovery_attempted: bool = False
    done_event: asyncio.Event = field(default_factory=asyncio.Event)
    mutex: threading.Lock = field(default_factory=threading.Lock, repr=False)


class RunManager:
    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._lock = asyncio.Lock()

    async def start_run(self, prompt: str, thread_id: str) -> RunRecord:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        record = RunRecord(run_id=run_id, prompt=prompt, thread_id=thread_id)
        async with self._lock:
            self._runs[run_id] = record

        self._push_event(
            record,
            event_type="run_started",
            actor="system",
            label="Run started",
            payload={"prompt": prompt, "thread_id": thread_id},
        )
        record.status = "running"
        threading.Thread(
            target=self._run_sync,
            args=(run_id,),
            daemon=True,
            name=f"run-worker-{run_id}",
        ).start()
        return record

    async def get_run(self, run_id: str) -> RunRecord | None:
        async with self._lock:
            return self._runs.get(run_id)

    def _run_sync(self, run_id: str) -> None:
        record = self._runs[run_id]
        agent = get_agent()
        seen_tool_calls: set[str] = set()
        seen_subagent_messages: set[str] = set()
        last_message_count = 0

        try:
            stream_iter = agent.stream(
                {"messages": [{"role": "user", "content": record.prompt}]},
                config={"configurable": {"thread_id": record.thread_id}},
                stream_mode="values",
            )

            self._push_event(
                record,
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
                            record,
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
                                record,
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

            source_required = prompt_requires_sources(record.prompt)
            if needs_recovery(record.prompt, final_answer):
                record.recovery_attempted = True
                self._push_event(
                    record,
                    event_type="recovery_attempted",
                    actor="system",
                    label="Recovering final answer",
                    payload={"success": False},
                )
                recovered = recover_final_answer(record.thread_id)
                if recovered:
                    final_answer = recovered
                    self._push_event(
                        record,
                        event_type="recovery_attempted",
                        actor="system",
                        label="Recovered final answer",
                        payload={"success": True},
                    )

            if source_required and not extract_citations(final_answer):
                record.recovery_attempted = True
                self._push_event(
                    record,
                    event_type="source_recovery_attempted",
                    actor="system",
                    label="Recovering answer with required source URLs",
                    payload={"success": False},
                )
                recovered_with_sources = recover_final_answer_with_sources(
                    record.thread_id, record.prompt
                )
                if recovered_with_sources:
                    final_answer = recovered_with_sources
                    self._push_event(
                        record,
                        event_type="source_recovery_attempted",
                        actor="system",
                        label="Recovered answer with required source URLs",
                        payload={"success": True},
                    )

            if not final_answer.strip():
                if not record.recovery_attempted:
                    record.recovery_attempted = True
                    self._push_event(
                        record,
                        event_type="recovery_attempted",
                        actor="system",
                        label="Recovering empty final answer",
                        payload={"success": False},
                    )
                    recovered = recover_final_answer(record.thread_id)
                    if recovered and recovered.strip():
                        final_answer = recovered
                        self._push_event(
                            record,
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
                        record,
                        event_type="final_answer_fallback",
                        actor="system",
                        label="Using fallback answer from prior message",
                        payload={},
                        level="warn",
                    )

            if not final_answer.strip():
                self._push_event(
                    record,
                    event_type="final_answer_missing",
                    actor="system",
                    label="Final answer missing",
                    payload={"reason": "Model returned empty content"},
                    level="error",
                )
                self._fail_run(record, "Model returned an empty final answer.")
                return

            if source_required and not extract_citations(final_answer):
                fallback_answer = direct_search_fallback_answer(record.prompt)
                if fallback_answer and extract_citations(fallback_answer):
                    final_answer = fallback_answer
                    self._push_event(
                        record,
                        event_type="direct_search_fallback",
                        actor="tool",
                        label="Recovered answer via direct internet_search fallback",
                        payload={"success": True},
                    )

            if source_required and not extract_citations(final_answer):
                self._push_event(
                    record,
                    event_type="final_answer_missing_sources",
                    actor="system",
                    label="Final answer missing source URLs",
                    payload={},
                    level="error",
                )
                self._fail_run(
                    record, "Model returned final answer without source URLs."
                )
                return

            record.final_answer = final_answer
            record.citations = extract_citations(final_answer)
            record.status = "completed"
            record.completed_at = _utc_now()
            record.duration_ms = int(
                (record.completed_at - record.started_at).total_seconds() * 1000
            )
            self._push_event(
                record,
                event_type="final_answer",
                actor="assistant",
                label="Final answer generated",
                payload={
                    "answer": final_answer,
                    "citations": record.citations,
                },
            )
            self._push_event(
                record,
                event_type="run_completed",
                actor="system",
                label="Run completed",
                payload={"duration_ms": record.duration_ms},
            )
            record.done_event.set()
        except RequestException as exc:
            self._fail_run(record, f"Network/API error while calling model: {exc}")
        except Exception as exc:  # noqa: BLE001
            self._fail_run(record, f"Run failed: {exc}")

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

    def _fail_run(self, record: RunRecord, message: str) -> None:
        record.status = "failed"
        record.error = message
        record.completed_at = _utc_now()
        record.duration_ms = int(
            (record.completed_at - record.started_at).total_seconds() * 1000
        )
        self._push_event(
            record,
            event_type="run_failed",
            actor="system",
            label="Run failed",
            payload={"error": message},
            level="error",
        )
        record.done_event.set()

    def _push_event(
        self,
        record: RunRecord,
        *,
        event_type: str,
        actor: str,
        label: str,
        payload: dict[str, Any] | None = None,
        level: str = "info",
    ) -> None:
        event = {
            "run_id": record.run_id,
            "timestamp": _iso(_utc_now()),
            "type": event_type,
            "actor": actor,
            "label": label,
            "payload": payload or {},
            "level": level,
        }
        with record.mutex:
            record.events.append(event)
            subscribers = list(record.subscribers)
        for queue in subscribers:
            queue.put_nowait(event)

    async def subscribe(self, run_id: str) -> asyncio.Queue[dict[str, Any]] | None:
        record = await self.get_run(run_id)
        if record is None:
            return None
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        with record.mutex:
            record.subscribers.add(queue)
        return queue

    async def unsubscribe(
        self, run_id: str, queue: asyncio.Queue[dict[str, Any]]
    ) -> None:
        record = await self.get_run(run_id)
        if record is None:
            return
        with record.mutex:
            record.subscribers.discard(queue)


def sse_pack(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
