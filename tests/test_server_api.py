from __future__ import annotations

import json
from pathlib import Path
import time
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


class SlowFakeAgent:
    def __init__(self, *, snapshots: list[dict[str, Any]], sleep_seconds: float = 0.15) -> None:
        self._snapshots = snapshots
        self._sleep_seconds = sleep_seconds

    def stream(self, *_args: Any, **_kwargs: Any):  # noqa: ANN002, ANN003
        for idx, snapshot in enumerate(self._snapshots):
            if idx > 0:
                time.sleep(self._sleep_seconds)
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


def _wait_for_completed(client: TestClient, run_id: str, timeout_seconds: float = 3.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = client.get(f"/runs/{run_id}")
        if response.status_code == 200 and response.json()["status"] in {
            "completed",
            "failed",
            "canceled",
        }:
            return
        time.sleep(0.02)
    raise AssertionError(f"Run did not complete in time. run_id={run_id}")


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


def test_list_runs_pagination_and_filters(  # type: ignore[no-untyped-def]
    monkeypatch, tmp_path: Path
) -> None:
    _patch_runtime_defaults(monkeypatch)
    fake_agent = FakeAgent(
        snapshots=[
            {"messages": [FakeMessage(content="Searching...")]},
            {"messages": [FakeMessage(content="Answer URL https://example.com/item")]},
        ]
    )
    monkeypatch.setattr(run_manager, "get_agent", lambda: fake_agent)
    monkeypatch.setattr(
        server, "manager", run_manager.RunManager(db_url=f"sqlite:///{tmp_path / 'runs.db'}")
    )

    client = TestClient(server.app)
    created: list[tuple[str, str]] = []
    for idx, thread_id in enumerate(["alpha", "alpha", "beta"], start=1):
        response = client.post(
            "/runs",
            json={"prompt": f"Prompt {idx}", "thread_id": thread_id},
        )
        assert response.status_code == 200
        run_id = response.json()["run_id"]
        created.append((run_id, thread_id))

    for run_id, _thread_id in created:
        _wait_for_completed(client, run_id)

    page = client.get("/runs", params={"limit": 2, "offset": 0})
    assert page.status_code == 200
    payload = page.json()
    assert payload["total"] >= 3
    assert payload["limit"] == 2
    assert payload["offset"] == 0
    assert len(payload["items"]) == 2
    assert all("event_count" in item for item in payload["items"])

    filtered = client.get("/runs", params={"thread_id": "beta", "status": "completed"})
    assert filtered.status_code == 200
    filtered_payload = filtered.json()
    assert filtered_payload["total"] == 1
    assert len(filtered_payload["items"]) == 1
    assert filtered_payload["items"][0]["thread_id"] == "beta"
    assert filtered_payload["items"][0]["status"] == "completed"


def test_cancel_run_endpoint_cancels_running_run(  # type: ignore[no-untyped-def]
    monkeypatch, tmp_path: Path
) -> None:
    _patch_runtime_defaults(monkeypatch)
    fake_agent = SlowFakeAgent(
        snapshots=[
            {"messages": [FakeMessage(content="Initial reasoning...")]},
            {"messages": [FakeMessage(content="Final answer https://example.com/final")]},
        ]
    )
    monkeypatch.setattr(run_manager, "get_agent", lambda: fake_agent)
    monkeypatch.setattr(
        server, "manager", run_manager.RunManager(db_url=f"sqlite:///{tmp_path / 'runs.db'}")
    )

    client = TestClient(server.app)
    create_response = client.post(
        "/runs",
        json={"prompt": "Cancel me", "thread_id": "cancel-thread"},
    )
    assert create_response.status_code == 200
    run_id = create_response.json()["run_id"]

    cancel_response = client.post(f"/runs/{run_id}/cancel")
    assert cancel_response.status_code == 200
    cancel_payload = cancel_response.json()
    assert cancel_payload["cancel_requested"] is True

    _wait_for_completed(client, run_id)
    summary = client.get(f"/runs/{run_id}").json()
    assert summary["status"] == "canceled"
    assert "canceled" in (summary["error"] or "").lower()


def test_retry_run_endpoint_creates_new_run_and_blocks_non_terminal(  # type: ignore[no-untyped-def]
    monkeypatch, tmp_path: Path
) -> None:
    _patch_runtime_defaults(monkeypatch)
    fake_agent = SlowFakeAgent(
        snapshots=[
            {"messages": [FakeMessage(content="Working...")]},
            {"messages": [FakeMessage(content="Final answer https://example.com/retry")]},
        ],
        sleep_seconds=0.1,
    )
    monkeypatch.setattr(run_manager, "get_agent", lambda: fake_agent)
    monkeypatch.setattr(
        server, "manager", run_manager.RunManager(db_url=f"sqlite:///{tmp_path / 'runs.db'}")
    )

    client = TestClient(server.app)
    create_response = client.post(
        "/runs",
        json={"prompt": "Retry me", "thread_id": "retry-thread"},
    )
    assert create_response.status_code == 200
    first_run_id = create_response.json()["run_id"]

    retry_while_running = client.post(f"/runs/{first_run_id}/retry")
    assert retry_while_running.status_code == 409

    _wait_for_completed(client, first_run_id)
    retry_response = client.post(f"/runs/{first_run_id}/retry")
    assert retry_response.status_code == 200
    second_run_id = retry_response.json()["run_id"]
    assert second_run_id != first_run_id

    _wait_for_completed(client, second_run_id)
    second_summary = client.get(f"/runs/{second_run_id}").json()
    assert second_summary["status"] == "completed"
