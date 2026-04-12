"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { RunEvent, RunListResponse, RunSummary } from "../lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

type TimelineFilter = "all" | "tools" | "subagents" | "errors";
type RunStatusFilter = "all" | "queued" | "running" | "completed" | "failed" | "canceled";
type RunStatus = "idle" | "running" | "completed" | "failed" | "canceled";

function getEventTime(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) {
    return timestamp;
  }
  return date.toLocaleTimeString();
}

function extractAnswer(event: RunEvent): string | null {
  const answer = event.payload.answer;
  if (typeof answer !== "string") return null;
  return answer.trim().length ? answer : null;
}

function matchesFilter(event: RunEvent, filter: TimelineFilter): boolean {
  if (filter === "all") return true;
  if (filter === "errors") return event.level === "error" || event.type.includes("failed");
  if (filter === "tools") return event.type.includes("tool");
  if (filter === "subagents") return event.type.includes("subagent");
  return true;
}

function eventDetail(event: RunEvent): string | null {
  if (event.type === "tool_called") {
    const name = typeof event.payload.name === "string" ? event.payload.name : null;
    return name ? `Invoked ${name}` : "Tool execution";
  }
  if (event.type === "subagent_update") {
    const preview =
      typeof event.payload.content_preview === "string" ? event.payload.content_preview : "";
    return preview || "Subagent progress update";
  }
  if (event.type === "run_failed") {
    const reason = typeof event.payload.error === "string" ? event.payload.error : "";
    return reason || "Run failed";
  }
  return null;
}

export default function HomePage() {
  const [prompt, setPrompt] = useState(
    "Use the researcher subagent to find the latest Kerala election timeline and include source URLs."
  );
  const [threadId, setThreadId] = useState("demo-thread");
  const [runId, setRunId] = useState<string | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [summary, setSummary] = useState<RunSummary | null>(null);
  const [runStatus, setRunStatus] = useState<RunStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<TimelineFilter>("all");
  const [recentRuns, setRecentRuns] = useState<RunSummary[]>([]);
  const [runsTotal, setRunsTotal] = useState(0);
  const [runsLimit] = useState(8);
  const [runsOffset, setRunsOffset] = useState(0);
  const [runsLoading, setRunsLoading] = useState(false);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [runStatusFilter, setRunStatusFilter] = useState<RunStatusFilter>("all");
  const [runsThreadFilter, setRunsThreadFilter] = useState("");
  const [autoRefreshRuns, setAutoRefreshRuns] = useState(false);
  const [autoRefreshSeconds, setAutoRefreshSeconds] = useState(10);
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    return () => {
      eventSourceRef.current?.close();
    };
  }, []);

  useEffect(() => {
    void refreshRuns();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runsOffset, runsLimit, runStatusFilter, runsThreadFilter]);

  useEffect(() => {
    if (!autoRefreshRuns) {
      return;
    }
    const intervalMs = Math.max(3, autoRefreshSeconds) * 1000;
    const timer = setInterval(() => {
      void refreshRuns();
    }, intervalMs);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRefreshRuns, autoRefreshSeconds]);

  const filteredEvents = useMemo(
    () => events.filter((event) => matchesFilter(event, filter)),
    [events, filter]
  );

  const finalAnswer = useMemo(() => {
    const fromSummary = summary?.final_answer;
    if (typeof fromSummary === "string" && fromSummary.trim().length) return fromSummary;
    const final = [...events].reverse().find((event) => event.type === "final_answer");
    return final ? extractAnswer(final) : null;
  }, [events, summary]);

  const hasMissingFinalAnswer = useMemo(() => {
    if (summary && summary.status === "failed" && summary.error?.includes("empty final answer")) {
      return true;
    }
    return events.some((event) => event.type === "final_answer_missing");
  }, [events, summary]);

  async function refreshSummary(id: string) {
    const response = await fetch(`${API_BASE}/runs/${id}`);
    if (!response.ok) {
      throw new Error(`Failed to fetch run summary (${response.status})`);
    }
    const payload = (await response.json()) as RunSummary;
    setSummary(payload);
  }

  async function refreshRuns() {
    setRunsLoading(true);
    setRunsError(null);
    const params = new URLSearchParams({
      limit: String(runsLimit),
      offset: String(runsOffset)
    });
    if (runStatusFilter !== "all") {
      params.set("status", runStatusFilter);
    }
    const threadFilterValue = runsThreadFilter.trim();
    if (threadFilterValue) {
      params.set("thread_id", threadFilterValue);
    }

    try {
      const response = await fetch(`${API_BASE}/runs?${params.toString()}`);
      if (!response.ok) {
        throw new Error(`Failed to fetch run list (${response.status})`);
      }
      const payload = (await response.json()) as RunListResponse;
      setRecentRuns(payload.items);
      setRunsTotal(payload.total);
    } catch (err) {
      setRunsError(err instanceof Error ? err.message : "Failed to load runs.");
    } finally {
      setRunsLoading(false);
    }
  }

  function connectStream(id: string) {
    eventSourceRef.current?.close();
    const source = new EventSource(`${API_BASE}/runs/${id}/stream`);
    eventSourceRef.current = source;

    source.onmessage = (msg) => {
      const event = JSON.parse(msg.data) as RunEvent;
      setEvents((prev) => [...prev, event]);

      if (event.type === "run_failed") {
        setRunStatus("failed");
        setError(String(event.payload.error ?? "Run failed"));
        source.close();
        void refreshSummary(id).catch(() => undefined);
      }
      if (event.type === "run_canceled") {
        setRunStatus("canceled");
        setError(String(event.payload.error ?? "Run canceled"));
        source.close();
        void refreshSummary(id).catch(() => undefined);
      }
      if (event.type === "run_completed") {
        setRunStatus("completed");
        source.close();
        void refreshSummary(id).catch(() => undefined);
      }
    };

    source.onerror = () => {
      setError("Lost stream connection to backend.");
      setRunStatus("failed");
      source.close();
    };
  }

  async function handleRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSummary(null);
    setEvents([]);
    setRunStatus("running");

    const response = await fetch(`${API_BASE}/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, thread_id: threadId })
    });
    if (!response.ok) {
      setRunStatus("failed");
      setError(`Failed to start run (${response.status})`);
      return;
    }
    const payload = (await response.json()) as { run_id: string };
    setRunId(payload.run_id);
    connectStream(payload.run_id);
    void refreshRuns();
  }

  async function handleCancelRun() {
    if (!runId) return;
    setError(null);
    const response = await fetch(`${API_BASE}/runs/${runId}/cancel`, { method: "POST" });
    if (!response.ok) {
      const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
      setError(payload?.detail ?? `Failed to cancel run (${response.status})`);
      return;
    }
    void refreshRuns();
    await refreshSummary(runId);
  }

  async function handleRetryRun() {
    if (!runId) return;
    setError(null);
    const response = await fetch(`${API_BASE}/runs/${runId}/retry`, { method: "POST" });
    if (!response.ok) {
      const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
      setError(payload?.detail ?? `Failed to retry run (${response.status})`);
      return;
    }
    const payload = (await response.json()) as { run_id: string; status: string };
    setRunId(payload.run_id);
    setSummary(null);
    setEvents([]);
    setRunStatus(payload.status === "running" ? "running" : "idle");
    connectStream(payload.run_id);
    void refreshRuns();
  }

  async function handleSelectRun(selectedRunId: string) {
    setError(null);
    setRunId(selectedRunId);
    setEvents([]);
    await refreshSummary(selectedRunId);
    connectStream(selectedRunId);
    const selected = recentRuns.find((item) => item.run_id === selectedRunId);
    if (selected) {
      const nextStatus: RunStatus =
        selected.status === "completed" ||
        selected.status === "failed" ||
        selected.status === "canceled"
          ? selected.status
          : "running";
      setRunStatus(nextStatus);
      setThreadId(selected.thread_id);
      setPrompt(selected.prompt);
    }
  }

  return (
    <main className="app-shell">
      <header className="hero">
        <p className="hero-kicker">Deep Agents</p>
        <h1>Operator Console</h1>
        <p>Run prompts with crisp real-time feedback, readable event traces, and reliable final outputs.</p>
      </header>

      <section className="grid">
        <article className="panel runs">
          <div className="runs-head">
            <h2>Recent Runs</h2>
            <button className="filter-btn" type="button" onClick={() => void refreshRuns()}>
              refresh
            </button>
          </div>
          <div className="runs-controls">
            <select
              value={runStatusFilter}
              onChange={(e) => {
                setRunsOffset(0);
                setRunStatusFilter(e.target.value as RunStatusFilter);
              }}
            >
              <option value="all">all statuses</option>
              <option value="queued">queued</option>
              <option value="running">running</option>
              <option value="completed">completed</option>
              <option value="failed">failed</option>
              <option value="canceled">canceled</option>
            </select>
            <input
              value={runsThreadFilter}
              onChange={(e) => {
                setRunsOffset(0);
                setRunsThreadFilter(e.target.value);
              }}
              placeholder="filter by thread_id"
            />
          </div>
          <div className="runs-auto">
            <label>
              <input
                type="checkbox"
                checked={autoRefreshRuns}
                onChange={(e) => setAutoRefreshRuns(e.target.checked)}
              />
              auto-refresh
            </label>
            <select
              value={String(autoRefreshSeconds)}
              onChange={(e) => setAutoRefreshSeconds(Number(e.target.value))}
              disabled={!autoRefreshRuns}
            >
              <option value="5">every 5s</option>
              <option value="10">every 10s</option>
              <option value="20">every 20s</option>
              <option value="30">every 30s</option>
            </select>
          </div>
          <ul className="runs-list">
            {recentRuns.map((item) => (
              <li key={item.run_id}>
                <button
                  className={`run-item ${runId === item.run_id ? "is-selected" : ""}`}
                  onClick={() => void handleSelectRun(item.run_id)}
                  type="button"
                >
                  <div>
                    <strong>{item.run_id}</strong>
                    <small>{item.thread_id}</small>
                  </div>
                  <div>
                    <span className={`chip chip-${item.status}`}>{item.status}</span>
                    <small>{item.event_count} events</small>
                  </div>
                </button>
              </li>
            ))}
            {!runsLoading && !recentRuns.length ? <li className="empty">No runs found.</li> : null}
          </ul>
          {runsError ? <p className="error">{runsError}</p> : null}
          <div className="runs-pagination">
            <button
              className="filter-btn"
              type="button"
              onClick={() => setRunsOffset((prev) => Math.max(0, prev - runsLimit))}
              disabled={runsOffset === 0 || runsLoading}
            >
              prev
            </button>
            <span>
              {runsOffset + 1}-{Math.min(runsOffset + runsLimit, runsTotal)} of {runsTotal}
            </span>
            <button
              className="filter-btn"
              type="button"
              onClick={() => setRunsOffset((prev) => prev + runsLimit)}
              disabled={runsOffset + runsLimit >= runsTotal || runsLoading}
            >
              next
            </button>
          </div>
        </article>

        <article className="panel composer">
          <h2>Prompt Composer</h2>
          <form onSubmit={handleRun}>
            <label htmlFor="thread">Thread ID</label>
            <input
              id="thread"
              value={threadId}
              onChange={(e) => setThreadId(e.target.value)}
              placeholder="demo-thread"
              required
            />

            <label htmlFor="prompt">Prompt</label>
            <textarea
              id="prompt"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={9}
              required
            />

            <button className="run-btn" disabled={runStatus === "running"} type="submit">
              {runStatus === "running" ? "Running..." : "Start Run"}
            </button>
          </form>

          <div className="status-row">
            <span className={`chip chip-${runStatus}`}>status: {runStatus}</span>
            {runId ? <span className="chip">run: {runId}</span> : null}
          </div>
          <div className="actions-row">
            <button
              className="filter-btn"
              type="button"
              onClick={() => void handleCancelRun()}
              disabled={!runId || runStatus !== "running"}
            >
              Cancel Run
            </button>
            <button
              className="filter-btn"
              type="button"
              onClick={() => void handleRetryRun()}
              disabled={!runId || !["completed", "failed", "canceled"].includes(runStatus)}
            >
              Retry Run
            </button>
          </div>
          {error ? <p className="error">{error}</p> : null}
        </article>

        <article className="panel timeline">
          <div className="timeline-top">
            <h2>Live Timeline</h2>
            <div className="filters">
              {(["all", "tools", "subagents", "errors"] as TimelineFilter[]).map((option) => (
                <button
                  type="button"
                  className={`filter-btn ${option === filter ? "active" : ""}`}
                  key={option}
                  onClick={() => setFilter(option)}
                >
                  {option}
                </button>
              ))}
            </div>
          </div>

          <ul>
            {filteredEvents.map((item, idx) => (
              <li key={`${item.timestamp}-${item.type}-${idx}`} className={item.level === "error" ? "is-error" : ""}>
                <time>{getEventTime(item.timestamp)}</time>
                <div className="event-main">
                  <div className="event-head">
                    <strong>{item.label}</strong>
                    <span className={`event-kind ${item.level === "error" ? "tone-error" : ""}`}>{item.type}</span>
                  </div>
                  <p>{item.actor}</p>
                  {eventDetail(item) ? <small>{eventDetail(item)}</small> : null}
                </div>
              </li>
            ))}
            {!filteredEvents.length ? <li className="empty">No events yet.</li> : null}
          </ul>
        </article>

        <article className="panel answer">
          <h2>Final Answer</h2>
          <div className="answer-body">{finalAnswer ?? "Run a prompt to see final output here."}</div>
          {hasMissingFinalAnswer ? (
            <p className="error">Model returned an empty final answer. Please retry the prompt.</p>
          ) : null}
          {summary?.citations?.length ? (
            <>
              <h3>Sources</h3>
              <ul className="sources">
                {summary.citations.map((url) => (
                  <li key={url}>
                    <a href={url} rel="noreferrer" target="_blank">
                      {url}
                    </a>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          {summary ? (
            <div className="meta">
              <span>events: {summary.event_count}</span>
              <span>duration: {summary.duration_ms ?? 0} ms</span>
              <span>recovered: {summary.recovery_attempted ? "yes" : "no"}</span>
            </div>
          ) : null}
        </article>
      </section>
    </main>
  );
}
