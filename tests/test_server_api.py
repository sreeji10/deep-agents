from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend import run_manager, server


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


def test_runs_api_and_sse_event_contract(  # type: ignore[no-untyped-def]
    monkeypatch, tmp_path: Path
) -> None:
    _patch_runtime_defaults(monkeypatch)
    fake_agent = FakeAgent(
        snapshots=[
            {
                "messages": [
                    FakeMessage(
                        content="Searching for evidence.",
                        tool_calls=[
                            {
                                "id": "tool-call-1",
                                "name": "internet_search",
                                "args": {"query": "SSE contract"},
                            }
                        ],
                    )
                ]
            },
            {
                "messages": [
                    FakeMessage(content="Final answer with URL https://example.com/source")
                ]
            },
        ]
    )
    monkeypatch.setattr(run_manager, "get_agent", lambda: fake_agent)
    monkeypatch.setattr(
        server, "manager", run_manager.RunManager(db_url=f"sqlite:///{tmp_path / 'runs.db'}")
    )

    client = TestClient(server.app)
    create_response = client.post(
        "/runs",
        json={"prompt": "Need cited answer", "thread_id": "api-thread"},
    )
    assert create_response.status_code == 200

    create_payload = create_response.json()
    run_id = create_payload["run_id"]

    stream_response = client.get(f"/runs/{run_id}/stream")
    assert stream_response.status_code == 200
    assert stream_response.headers["content-type"].startswith("text/event-stream")

    data_lines = [
        line for line in stream_response.text.splitlines() if line.startswith("data: ")
    ]
    assert data_lines, "Expected at least one SSE data frame"

    required_keys = {"run_id", "timestamp", "type", "actor", "label", "payload", "level"}
    for line in data_lines:
        event = json.loads(line[len("data: ") :])
        assert required_keys.issubset(event.keys())
        assert event["run_id"] == run_id

    summary_response = client.get(f"/runs/{run_id}")
    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary["status"] == "completed"
    assert summary["final_answer"] is not None
    assert summary["event_count"] >= 4
