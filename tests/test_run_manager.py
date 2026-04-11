from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from backend import run_manager


class FakeMessage:
    def __init__(
        self,
        content: str = "",
        *,
        tool_calls: list[dict[str, Any]] | None = None,
        name: str | None = None,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls or []
        self.name = name


class FakeAgent:
    def __init__(self, *, snapshots: list[dict[str, Any]]) -> None:
        self._snapshots = snapshots

    def stream(self, *_args: Any, **_kwargs: Any):  # noqa: ANN002, ANN003
        for snapshot in self._snapshots:
            yield snapshot


def wait_for_terminal_state(
    manager: run_manager.RunManager, run_id: str, timeout_seconds: float = 3.0
) -> run_manager.RunRecord:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        record = asyncio.run(manager.get_run(run_id))
        if record is None:
            break
        if record.status in {"completed", "failed"}:
            return record
        time.sleep(0.01)
    raise AssertionError(f"Run did not reach terminal state before timeout. run_id={run_id}")


def _patch_runtime_defaults(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(run_manager, "prompt_requires_sources", lambda _prompt: False)
    monkeypatch.setattr(run_manager, "needs_recovery", lambda _prompt, _answer: False)
    monkeypatch.setattr(run_manager, "recover_final_answer", lambda _thread_id: None)
    monkeypatch.setattr(
        run_manager,
        "recover_final_answer_with_sources",
        lambda _thread_id, _original_prompt: None,
    )
    monkeypatch.setattr(
        run_manager, "direct_search_fallback_answer", lambda _prompt: None
    )


def test_run_manager_completes_and_emits_expected_events(  # type: ignore[no-untyped-def]
    monkeypatch, tmp_path: Path
) -> None:
    _patch_runtime_defaults(monkeypatch)
    fake_agent = FakeAgent(
        snapshots=[
            {
                "messages": [
                    FakeMessage(
                        content="Collected context from sources.",
                        name="researcher",
                        tool_calls=[
                            {
                                "id": "tool-call-1",
                                "name": "internet_search",
                                "args": {"query": "Kerala election timeline"},
                            }
                        ],
                    )
                ]
            },
            {
                "messages": [
                    FakeMessage(
                        content="Kerala timeline summary. Source: https://example.com/kerala"
                    )
                ]
            },
        ]
    )
    monkeypatch.setattr(run_manager, "get_agent", lambda: fake_agent)

    manager = run_manager.RunManager(db_url=f"sqlite:///{tmp_path / 'runs.db'}")
    started_record = asyncio.run(
        manager.start_run(
            prompt="Find Kerala timeline with sources",
            thread_id="test-thread",
        )
    )
    record = wait_for_terminal_state(manager, started_record.run_id)

    assert record.status == "completed"
    assert record.final_answer is not None
    assert "https://example.com/kerala" in record.final_answer
    assert record.citations == ["https://example.com/kerala"]

    event_types = [event["type"] for event in record.events]
    assert "run_started" in event_types
    assert "model_started" in event_types
    assert "tool_called" in event_types
    assert "subagent_update" in event_types
    assert "final_answer" in event_types
    assert "run_completed" in event_types


def test_run_manager_recovers_placeholder_final_answer(  # type: ignore[no-untyped-def]
    monkeypatch, tmp_path: Path
) -> None:
    _patch_runtime_defaults(monkeypatch)
    fake_agent = FakeAgent(
        snapshots=[
            {
                "messages": [
                    FakeMessage(content="I will provide the answer shortly.")
                ]
            }
        ]
    )
    monkeypatch.setattr(run_manager, "get_agent", lambda: fake_agent)
    monkeypatch.setattr(run_manager, "needs_recovery", lambda _prompt, _answer: True)
    monkeypatch.setattr(
        run_manager,
        "recover_final_answer",
        lambda _thread_id: "Recovered answer with source https://example.com/recovered",
    )

    manager = run_manager.RunManager(db_url=f"sqlite:///{tmp_path / 'runs.db'}")
    started_record = asyncio.run(
        manager.start_run(prompt="Any prompt", thread_id="test-thread-recovery")
    )
    record = wait_for_terminal_state(manager, started_record.run_id)

    assert record.status == "completed"
    assert record.recovery_attempted is True
    assert record.final_answer is not None
    assert "https://example.com/recovered" in record.final_answer
    recovery_events = [
        event
        for event in record.events
        if event["type"] == "recovery_attempted" and event["payload"].get("success")
    ]
    assert recovery_events, "Expected successful recovery event in timeline"


def test_run_manager_fails_when_sources_required_but_missing(  # type: ignore[no-untyped-def]
    monkeypatch, tmp_path: Path
) -> None:
    _patch_runtime_defaults(monkeypatch)
    fake_agent = FakeAgent(
        snapshots=[{"messages": [FakeMessage(content="Answer without citations.")]}]
    )
    monkeypatch.setattr(run_manager, "get_agent", lambda: fake_agent)
    monkeypatch.setattr(run_manager, "prompt_requires_sources", lambda _prompt: True)

    manager = run_manager.RunManager(db_url=f"sqlite:///{tmp_path / 'runs.db'}")
    started_record = asyncio.run(
        manager.start_run(
            prompt="Answer with source URLs required",
            thread_id="test-thread-sources",
        )
    )
    record = wait_for_terminal_state(manager, started_record.run_id)

    assert record.status == "failed"
    assert record.error == "Model returned final answer without source URLs."
    event_types = [event["type"] for event in record.events]
    assert "source_recovery_attempted" in event_types
    assert "final_answer_missing_sources" in event_types
    assert "run_failed" in event_types
