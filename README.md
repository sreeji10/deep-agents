# Deep Agents + Operator Console

This repo now includes:
- A Python CLI (`main.py`) for quick prompt runs.
- A FastAPI backend with live SSE run events (`backend/server.py`).
- A Next.js frontend console (`ui/`) for prompt execution feedback.

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
- `GET /runs/{run_id}/stream` live timeline via SSE
- `GET /runs/{run_id}` run summary + final answer

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
```
