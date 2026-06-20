# Deep Agents + Operator Console

This project provides a research agent with web search, a FastAPI run service, and
a Next.js operator console.

Current capabilities include:
- A Python CLI (`main.py`) for quick prompt runs.
- Persistent run history backed by SQLite.
- Live run timelines over server-sent events (SSE).
- Run filtering, cancellation, retry, and configurable auto-refresh.
- Final-answer recovery and source URL validation for research prompts.

## 1) Backend setup

```powershell
uv sync
uv run uvicorn backend.server:app --reload --port 8000
```

If you are already inside `backend/`, run:

```powershell
uv run uvicorn server:app --reload --port 8000
```

Backend endpoints:
- `POST /runs` start a prompt run
- `GET /runs` list and filter recent runs
- `GET /runs/{run_id}/stream` live timeline via SSE
- `GET /runs/{run_id}` run summary + final answer
- `POST /runs/{run_id}/cancel` request cancellation
- `POST /runs/{run_id}/retry` retry a terminal run
- `GET /health` service health check

Run data is stored in `.data/runs.db` by default. Set `RUN_DB_URL` to use a
different SQLAlchemy database URL.

## 2) Frontend setup

```powershell
cd ui
pnpm install
pnpm dev
```

Optional API base override:

```powershell
$env:NEXT_PUBLIC_API_BASE="http://127.0.0.1:8000"
```

Open `http://127.0.0.1:3000`.

## 3) Environment variables

Create `.env` in repo root:

```env
MODEL_NAME=nvidia/nemotron-3-nano-30b-a3b
API_KEY=your_nvidia_api_key
DEBUG_TRACE=0
RUN_DB_URL=sqlite:///./.data/runs.db
```

## 4) Tests

Run the backend test suite with:

```powershell
uv run pytest
```

Build and type-check the frontend with:

```powershell
cd ui
pnpm build
```
