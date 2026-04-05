export type RunEvent = {
  run_id: string;
  timestamp: string;
  type: string;
  actor: string;
  label: string;
  payload: Record<string, unknown>;
  level: "info" | "error" | "warn";
};

export type RunSummary = {
  run_id: string;
  status: string;
  prompt: string;
  thread_id: string;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  final_answer: string | null;
  citations: string[];
  error: string | null;
  recovery_attempted: boolean;
  event_count: number;
};
