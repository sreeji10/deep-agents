"use client";

import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { RunEvent, RunListResponse, RunSummary } from "../lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

type TimelineFilter = "all" | "tools" | "subagents" | "errors";
type RunStatusFilter = "all" | "queued" | "running" | "completed" | "failed" | "canceled";
type RunStatus = "idle" | "running" | "completed" | "failed" | "canceled";
type ConnectionState = "idle" | "connecting" | "connected" | "disconnected" | "completed";
type EventCategory = "system" | "model" | "tool" | "subagent" | "warning" | "error" | "completion";
const STORAGE_KEYS = {
  runStatusFilter: "deep_agents.run_status_filter",
  runsThreadFilter: "deep_agents.runs_thread_filter",
} as const;

function getEventTime(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) {
    return timestamp;
  }
  return date.toLocaleTimeString();
}

function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const diff = Date.now() - date.getTime();
  if (diff < 60000) return "just now";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
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

function categorizeEvent(event: RunEvent): EventCategory {
  if (event.level === "error") return "error";
  if (event.level === "warn") return "warning";
  if (event.type.includes("tool")) return "tool";
  if (event.type.includes("subagent")) return "subagent";
  if (event.type.includes("model")) return "model";
  if (event.type === "run_completed" || event.type === "final_answer") return "completion";
  if (event.type.startsWith("run_")) return "system";
  return "system";
}

function eventSummary(event: RunEvent): string {
  switch (event.type) {
    case "tool_called": {
      const name = event.payload.name;
      return typeof name === "string" ? name : "Tool execution";
    }
    case "tool_result": return "Tool completed";
    case "subagent_update": {
      const preview = event.payload.content_preview;
      return typeof preview === "string" ? preview.slice(0, 100) : "Subagent progress";
    }
    case "run_started": return "Run started";
    case "run_completed": return "Completed successfully";
    case "run_failed": {
      const err = event.payload.error;
      return typeof err === "string" ? err.slice(0, 120) : "Run encountered an error";
    }
    case "run_canceled": return "Run canceled";
    case "model_call": return "Calling model...";
    case "model_response": return "Model response received";
    case "final_answer": return "Final answer ready";
    default: return event.label || event.type;
  }
}

function eventPayloadPreview(event: RunEvent): string {
  const payload = event.payload;
  if (event.type === "tool_called" && typeof payload.arguments === "string") {
    return payload.arguments;
  }
  return JSON.stringify(payload, null, 2);
}

export default function HomeClient() {
  const [prompt, setPrompt] = useState("");
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
  const [prefsLoaded, setPrefsLoaded] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [connectionState, setConnectionState] = useState<ConnectionState>("idle");
  const [expandedSet, setExpandedSet] = useState<Set<number>>(new Set());
  const [autoScroll, setAutoScroll] = useState(true);
  const [workspaceTab, setWorkspaceTab] = useState<"answer" | "timeline">("answer");
  const [copied, setCopied] = useState(false);
  const copiedTimeoutRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const timelineListRef = useRef<HTMLUListElement>(null);
  const completedRef = useRef(false);

  const examples = [
    "What is the capital of France?",
    "Summarize the key findings from the latest IPCC climate report",
    "Explain quantum computing in simple terms",
  ];

  useEffect(() => {
    return () => {
      eventSourceRef.current?.close();
    };
  }, []);

  useEffect(() => {
    try {
      const storedStatus = localStorage.getItem(STORAGE_KEYS.runStatusFilter);
      if (
        storedStatus === "all" ||
        storedStatus === "queued" ||
        storedStatus === "running" ||
        storedStatus === "completed" ||
        storedStatus === "failed" ||
        storedStatus === "canceled"
      ) {
        setRunStatusFilter(storedStatus);
      }

      const storedThread = localStorage.getItem(STORAGE_KEYS.runsThreadFilter);
      if (typeof storedThread === "string") {
        setRunsThreadFilter(storedThread);
      }
    } finally {
      setPrefsLoaded(true);
    }
  }, []);

  useEffect(() => {
    if (!prefsLoaded) return;
    localStorage.setItem(STORAGE_KEYS.runStatusFilter, runStatusFilter);
  }, [prefsLoaded, runStatusFilter]);

  useEffect(() => {
    if (!prefsLoaded) return;
    localStorage.setItem(STORAGE_KEYS.runsThreadFilter, runsThreadFilter);
  }, [prefsLoaded, runsThreadFilter]);

  useEffect(() => {
    fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(5000) })
      .then((res) => {
        if (!res.ok) throw new Error(`Health check failed (${res.status})`);
        setConnectError(null);
      })
      .catch(() => setConnectError("Cannot reach the backend API server."));
  }, []);

  useEffect(() => {
    if (!prefsLoaded) return;
    void fetchRunsAtOffset(0, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefsLoaded]);

  useEffect(() => {
    if (!prefsLoaded) return;
    const params = new URLSearchParams(window.location.search);
    const urlRunId = params.get("run_id");
    if (urlRunId && urlRunId !== runId) {
      setRunId(urlRunId);
      setEvents([]);
      (async () => {
        try {
          const s = await refreshSummary(urlRunId);
          setRunStatus(s.status as RunStatus);
          setThreadId(s.thread_id);
          setPrompt(s.prompt);
          connectStream(urlRunId);
        } catch {
          setError("Failed to load run from URL.");
        }
      })();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefsLoaded]);

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

  const filterCounts = useMemo(() => {
    const counts = { all: events.length, tools: 0, subagents: 0, errors: 0 };
    for (const event of events) {
      if (matchesFilter(event, "tools")) counts.tools++;
      if (matchesFilter(event, "subagents")) counts.subagents++;
      if (matchesFilter(event, "errors")) counts.errors++;
    }
    return counts;
  }, [events]);

  const answerState = useMemo(() => {
    if (!runId) return "idle";
    if (hasMissingFinalAnswer) return "missing";
    if (runStatus === "completed" && finalAnswer) return "completed";
    if (runStatus === "completed" && !finalAnswer) return "missing";
    if (runStatus === "failed" || runStatus === "canceled") return "failed";
    return "generating";
  }, [runId, runStatus, finalAnswer, hasMissingFinalAnswer]);

  async function refreshSummary(id: string): Promise<RunSummary> {
    const response = await fetch(`${API_BASE}/runs/${id}`);
    if (!response.ok) {
      throw new Error(`Failed to fetch run summary (${response.status})`);
    }
    const payload = (await response.json()) as RunSummary;
    setSummary(payload);
    return payload;
  }

  async function refreshRuns() {
    setRunsOffset(0);
    await fetchRunsAtOffset(0, false);
  }

  async function fetchRunsAtOffset(offset: number, append: boolean) {
    setRunsLoading(true);
    setRunsError(null);
    const params = new URLSearchParams({
      limit: String(runsLimit),
      offset: String(offset)
    });
    if (runStatusFilter !== "all") params.set("status", runStatusFilter);
    const threadFilterValue = runsThreadFilter.trim();
    if (threadFilterValue) params.set("thread_id", threadFilterValue);

    try {
      const response = await fetch(`${API_BASE}/runs?${params.toString()}`);
      if (!response.ok) throw new Error(`Failed to fetch run list (${response.status})`);
      const payload = (await response.json()) as RunListResponse;
      setRecentRuns(prev => append ? [...prev, ...payload.items] : payload.items);
      setRunsTotal(payload.total);
    } catch (err) {
      setRunsError(err instanceof Error ? err.message : "Failed to load runs.");
    } finally {
      setRunsLoading(false);
    }
  }

  async function loadMoreRuns() {
    const nextOffset = runsOffset + runsLimit;
    await fetchRunsAtOffset(nextOffset, true);
    setRunsOffset(nextOffset);
  }

  function connectStream(id: string) {
    eventSourceRef.current?.close();
    completedRef.current = false;
    setConnectionState("connecting");
    const source = new EventSource(`${API_BASE}/runs/${id}/stream`);
    eventSourceRef.current = source;

    source.onopen = () => {
      if (!completedRef.current) setConnectionState("connected");
    };

    source.onmessage = (msg) => {
      if (!completedRef.current) setConnectionState("connected");
      const event = JSON.parse(msg.data) as RunEvent;
      setEvents((prev) => [...prev, event]);

      if (event.type === "run_failed") {
        completedRef.current = true;
        setRunStatus("failed");
        setConnectionState("completed");
        setError(String(event.payload.error ?? "Run failed"));
        source.close();
        void refreshSummary(id).catch(() => undefined);
      }
      if (event.type === "run_canceled") {
        completedRef.current = true;
        setRunStatus("canceled");
        setConnectionState("completed");
        setError(String(event.payload.error ?? "Run canceled"));
        source.close();
        void refreshSummary(id).catch(() => undefined);
      }
      if (event.type === "run_completed") {
        completedRef.current = true;
        setRunStatus("completed");
        setConnectionState("completed");
        setWorkspaceTab("answer");
        source.close();
        void refreshSummary(id).catch(() => undefined);
      }
    };

    source.onerror = () => {
      if (completedRef.current) return;
      setConnectionState("disconnected");
      setError("Lost stream connection to backend.");
      setRunStatus("failed");
      source.close();
    };
  }

  async function handleRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setValidationError(null);
    setError(null);

    const trimmed = prompt.trim();
    if (!trimmed) {
      setValidationError("Please enter a prompt.");
      return;
    }

    setConnectionState("idle");
    setExpandedSet(new Set());
    setWorkspaceTab("answer");
    setSummary(null);
    setEvents([]);
    setSubmitting(true);

    const response = await fetch(`${API_BASE}/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: trimmed, thread_id: threadId })
    });
    setSubmitting(false);
    if (!response.ok) {
      setRunStatus("failed");
      setError(`Failed to start run (${response.status})`);
      return;
    }
    const payload = (await response.json()) as { run_id: string };
    setRunId(payload.run_id);
    setRunStatus("running");
    connectStream(payload.run_id);
    window.history.pushState(null, "", `?run_id=${payload.run_id}`);
    void refreshRuns();
  }

  async function handleCancelRun() {
    if (!runId) return;
    setError(null);
    setValidationError(null);
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
    setValidationError(null);
    const response = await fetch(`${API_BASE}/runs/${runId}/retry`, { method: "POST" });
    if (!response.ok) {
      const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
      setError(payload?.detail ?? `Failed to retry run (${response.status})`);
      return;
    }
    const payload = (await response.json()) as { run_id: string; status: string };
    setRunId(payload.run_id);
    setConnectionState("idle");
    setExpandedSet(new Set());
    setWorkspaceTab("answer");
    setSummary(null);
    setEvents([]);
    setRunStatus(payload.status === "running" ? "running" : "idle");
    connectStream(payload.run_id);
    window.history.pushState(null, "", `?run_id=${payload.run_id}`);
    void refreshRuns();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      const form = (event.target as HTMLTextAreaElement).closest("form");
      if (form) {
        form.requestSubmit();
      }
    }
  }

  async function handleSelectRun(selectedRunId: string) {
    setError(null);
    setValidationError(null);
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
    setConnectionState("idle");
    setWorkspaceTab("answer");
    setSidebarOpen(false);
    window.history.pushState(null, "", `?run_id=${selectedRunId}`);
  }

  function toggleEvent(idx: number) {
    setExpandedSet((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  }

  function handleTimelineScroll() {
    const el = timelineListRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    setAutoScroll(atBottom);
  }

  function handleJumpToLatest() {
    setAutoScroll(true);
    timelineListRef.current?.scrollTo({ top: timelineListRef.current.scrollHeight, behavior: "smooth" });
  }

  async function handleCopyAnswer() {
    if (!finalAnswer) return;
    try {
      await navigator.clipboard.writeText(finalAnswer);
      setCopied(true);
      if (copiedTimeoutRef.current) clearTimeout(copiedTimeoutRef.current);
      copiedTimeoutRef.current = setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard not available
    }
  }

  function handleExportAnswer() {
    if (!finalAnswer) return;
    const blob = new Blob([finalAnswer], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `answer-${runId ?? "run"}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  useEffect(() => {
    if (autoScroll && timelineListRef.current) {
      timelineListRef.current.scrollTop = timelineListRef.current.scrollHeight;
    }
  }, [events, autoScroll]);

  return (
    <div className="app-shell">
      {connectError ? <div className="connect-error">{connectError}</div> : null}

      <div className="app-layout">
        <aside className={`sidebar ${sidebarOpen ? "sidebar--open" : ""}`}>
          <div className="sidebar-header">
            <span className="sidebar-title">Runs</span>
            <button className="sidebar-close" type="button" onClick={() => setSidebarOpen(false)} aria-label="Close sidebar">
              &times;
            </button>
          </div>

          <div className="sidebar-controls">
            <select
              value={runStatusFilter}
              onChange={(e) => {
                setRunStatusFilter(e.target.value as RunStatusFilter);
                void refreshRuns();
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
                setRunsThreadFilter(e.target.value);
                void refreshRuns();
              }}
              placeholder="search thread..."
            />
          </div>

          <div className="sidebar-actions">
            <button className="btn btn--secondary" type="button" onClick={() => void refreshRuns()} disabled={runsLoading}>
              {runsLoading ? "loading..." : "refresh"}
            </button>
          </div>

          <ul className="sidebar-list">
            {recentRuns.map((item) => (
              <li key={item.run_id}>
                <button
                  className={`sidebar-item ${runId === item.run_id ? "sidebar-item--selected" : ""}`}
                  onClick={() => void handleSelectRun(item.run_id)}
                  type="button"
                >
                  <div className="sidebar-item-top">
                    <span className={`status-dot status-dot--${item.status}`} />
                    <span className="sidebar-item-id">{item.run_id.slice(0, 10)}</span>
                    <span className="sidebar-item-time">{formatTime(item.started_at)}</span>
                  </div>
                  <div className="sidebar-item-prompt">{item.prompt.slice(0, 70)}{item.prompt.length > 70 ? "..." : ""}</div>
                  <div className="sidebar-item-bottom">
                    <span className={`chip chip-${item.status}`}>{item.status}</span>
                    <small>{item.event_count} events</small>
                    {item.duration_ms ? <small>{item.duration_ms} ms</small> : null}
                  </div>
                </button>
              </li>
            ))}
            {!runsLoading && !recentRuns.length ? <li className="empty">No runs found.</li> : null}
          </ul>

          {runsError ? <p className="error">{runsError}</p> : null}

          {runsOffset + runsLimit < runsTotal ? (
            <div className="sidebar-footer">
              <button
                className="btn btn--secondary sidebar-load-more"
                type="button"
                onClick={() => void loadMoreRuns()}
                disabled={runsLoading}
              >
                {runsLoading ? "loading..." : `load more (${runsTotal - runsOffset - runsLimit} remaining)`}
              </button>
            </div>
          ) : null}
        </aside>

        <div className="workspace">
          <div className="workspace-topbar">
            <button className="btn btn--icon" type="button" onClick={() => setSidebarOpen(true)} aria-label="Open sidebar">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="18" x2="21" y2="18" />
              </svg>
            </button>
          </div>

          {runId ? (
            <div className="workspace-header">
              <div className="workspace-header-left">
                <span className={`status-dot status-dot--${runStatus}`} />
                <span className="workspace-run-id">{runId}</span>
                <span className={`chip chip-${runStatus}`}>{runStatus}</span>
                {summary?.duration_ms ? <span className="workspace-duration">{summary.duration_ms} ms</span> : null}
              </div>
              <div className="workspace-header-actions">
                <button
                  className="btn btn--secondary"
                  type="button"
                  onClick={() => void handleCancelRun()}
                  disabled={!runId || runStatus !== "running"}
                >
                  Cancel
                </button>
                <button
                  className="btn btn--secondary"
                  type="button"
                  onClick={() => void handleRetryRun()}
                  disabled={!runId || !["completed", "failed", "canceled"].includes(runStatus)}
                >
                  Retry
                </button>
              </div>
            </div>
          ) : null}

          {error ? <div className="workspace-error">{error}</div> : null}

          <div className="workspace-panels">
            <section className="panel panel--composer">
              <div className="panel-header">
                <h2 className="panel-title">Composer</h2>
              </div>
              <form onSubmit={handleRun}>
                <textarea
                  ref={textareaRef}
                  id="prompt"
                  name="prompt"
                  value={prompt}
                  onChange={(e) => {
                    setPrompt(e.target.value);
                    if (validationError) setValidationError(null);
                  }}
                  onKeyDown={handleKeyDown}
                  rows={4}
                  placeholder="Ask anything..."
                  autoComplete="off"
                  disabled={submitting || runStatus === "running"}
                />

                {validationError ? <p className="field-error">{validationError}</p> : null}

                {!prompt.trim() && !runId ? (
                  <div className="examples">
                    {examples.map((ex) => (
                      <button
                        key={ex}
                        type="button"
                        className="example-chip"
                        onClick={() => {
                          setPrompt(ex);
                          textareaRef.current?.focus();
                        }}
                      >
                        {ex}
                      </button>
                    ))}
                  </div>
                ) : null}

                <div className="composer-footer">
                  <div className="composer-footer-left">
                    <span className="key-hint">Ctrl+Enter to submit</span>
                  </div>
                  <div className="composer-footer-right">
                    <button
                      className="btn btn--secondary btn--sm"
                      type="button"
                      onClick={() => setAdvancedOpen(!advancedOpen)}
                    >
                      {advancedOpen ? "Hide" : "Advanced"}
                    </button>
                    <button
                      className="btn btn--primary"
                      disabled={submitting || runStatus === "running"}
                      type="submit"
                    >
                      {submitting ? "Starting..." : runStatus === "running" ? "Running..." : "Run"}
                    </button>
                  </div>
                </div>

                {advancedOpen ? (
                  <div className="advanced-section">
                    <label htmlFor="thread">Thread ID</label>
                    <input
                      id="thread"
                      name="thread"
                      value={threadId}
                      onChange={(e) => setThreadId(e.target.value)}
                      placeholder="demo-thread"
                      autoComplete="off"
                    />
                  </div>
                ) : null}
              </form>
            </section>

            <div className="tab-bar">
              <button
                className={`tab ${workspaceTab === "answer" ? "tab--active" : ""}`}
                onClick={() => setWorkspaceTab("answer")}
                type="button"
              >
                Answer
              </button>
              <button
                className={`tab ${workspaceTab === "timeline" ? "tab--active" : ""}`}
                onClick={() => setWorkspaceTab("timeline")}
                type="button"
              >
                Timeline
                {events.length > 0 ? <span className="tab-count">{events.length}</span> : null}
              </button>
            </div>

            {workspaceTab === "answer" ? (
              <section className="panel panel--answer">
                {answerState === "idle" ? (
                  <div className="answer-empty">
                    <p>Run a prompt to see the final answer here.</p>
                  </div>
                ) : null}

                {answerState === "generating" ? (
                  <div className="answer-generating">
                    <span className="gen-indicator" />
                    <span>Generating answer...</span>
                  </div>
                ) : null}

                {answerState === "completed" ? (
                  <>
                    <div className="answer-toolbar">
                      <button
                        className="btn btn--secondary btn--sm"
                        onClick={handleCopyAnswer}
                        type="button"
                      >
                        {copied ? "Copied!" : "Copy"}
                      </button>
                      <button
                        className="btn btn--secondary btn--sm"
                        onClick={handleExportAnswer}
                        type="button"
                      >
                        Export
                      </button>
                    </div>
                    <div className="answer-body markdown-body">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {finalAnswer ?? ""}
                      </ReactMarkdown>
                    </div>
                    {summary?.citations?.length ? (
                      <div className="sources-section">
                        <h3 className="sources-title">Sources</h3>
                        <ul className="sources">
                          {summary.citations.map((url) => (
                            <li key={url}>
                              <a href={url} rel="noreferrer" target="_blank">{url}</a>
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                    {summary ? (
                      <div className="workspace-meta">
                        <span>events: {summary.event_count}</span>
                        <span>duration: {summary.duration_ms ?? 0} ms</span>
                        <span>recovered: {summary.recovery_attempted ? "yes" : "no"}</span>
                      </div>
                    ) : null}
                  </>
                ) : null}

                {answerState === "missing" ? (
                  <div className="answer-missing">
                    <p>Model returned an empty final answer. Please retry the prompt.</p>
                  </div>
                ) : null}

                {answerState === "failed" ? (
                  <div className="answer-failed">
                    <p>{error ?? "The run failed."}</p>
                  </div>
                ) : null}
              </section>
            ) : (
              <section className="panel panel--timeline">
                <div className="panel-header">
                  <h2 className="panel-title">Timeline</h2>
                  {events.length > 0 ? (
                    <div className="timeline-connection">
                      <span className={`conn-dot conn-dot--${connectionState}`} />
                      <span className="conn-label">{connectionState}</span>
                    </div>
                  ) : null}
                </div>

                {events.length > 0 ? (
                  <>
                    <div className="timeline-filters">
                      {(["all", "tools", "subagents", "errors"] as TimelineFilter[]).map((option) => {
                        if (option !== "all" && filterCounts[option] === 0) return null;
                        return (
                          <button
                            type="button"
                            className={`btn btn--tag ${option === filter ? "btn--tag-active" : ""}`}
                            key={option}
                            onClick={() => setFilter(option)}
                          >
                            {option}
                            {option !== "all" ? ` (${filterCounts[option]})` : null}
                          </button>
                        );
                      })}
                    </div>

                    <div className="timeline-scroll-wrap">
                      <ul
                        ref={timelineListRef}
                        className="timeline-list"
                        onScroll={handleTimelineScroll}
                      >
                        {filteredEvents.map((item, idx) => {
                          const isExpanded = expandedSet.has(idx);
                          const cat = categorizeEvent(item);

                          return (
                            <li
                              key={`${item.timestamp}-${item.type}-${idx}`}
                              className={`tl-row tl-row--${cat} ${isExpanded ? "tl-row--expanded" : ""}`}
                            >
                              <button
                                type="button"
                                className="tl-row-main"
                                onClick={() => toggleEvent(idx)}
                              >
                                <time className="tl-time">{getEventTime(item.timestamp)}</time>
                                <span className={`tl-badge tl-badge--${cat}`} />
                                <span className="tl-label">{eventSummary(item)}</span>
                                <span className="tl-type">{item.type}</span>
                                <span className="tl-actor">{item.actor}</span>
                                <span className="tl-chevron">{isExpanded ? "▲" : "▼"}</span>
                              </button>

                              {isExpanded ? (
                                <pre className="tl-detail">{eventPayloadPreview(item)}</pre>
                              ) : null}
                            </li>
                          );
                        })}
                      </ul>

                      {runStatus === "running" && !autoScroll ? (
                        <button
                          type="button"
                          className="btn btn--jump"
                          onClick={handleJumpToLatest}
                        >
                          ↓ Latest
                        </button>
                      ) : null}
                    </div>
                  </>
                ) : (
                  <div className="empty">No events yet.</div>
                )}
              </section>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
