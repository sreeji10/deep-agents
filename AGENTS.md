# Repository Guidelines

## Project Structure & Module Organization
- `backend/` contains the FastAPI server, run manager, and agent runtime.
- `tools/` holds reusable tools such as `internet_search`.
- `ui/` is the Next.js console for run submission, live SSE updates, and final answers.
- `memory/AGENTS.md` stores agent memory and operating notes.
- `skills/` contains agent skills; current project skill lives at `skills/project/web-research/`.
- `main.py` is a local CLI entrypoint for quick prompt runs.

## Build, Test, and Development Commands
- `uv sync` installs Python dependencies from `pyproject.toml` and `uv.lock`.
- `uv run uvicorn backend.server:app --reload --port 8000` starts the API locally.
- `uv run python main.py` runs the CLI prompt menu.
- `cd ui && pnpm install` installs frontend dependencies.
- `cd ui && pnpm dev` starts the Next.js console on port `3000`.
- `pre-commit run --all-files` runs the repo hooks, including Ruff formatting.

## Coding Style & Naming Conventions
- Use 4-space indentation in Python and 2-space indentation in frontend files.
- Prefer `snake_case` for Python functions, variables, and module names.
- Prefer `PascalCase` for React components and `camelCase` for client-side variables.
- Keep changes compatible with `ruff-format` and the existing `.editorconfig` rules.
- Use descriptive names for run events and payload fields, matching the existing backend event schema.

## Testing Guidelines
- There is no dedicated test suite yet. Add tests with new behavior, especially for run orchestration and recovery logic.
- Prefer deterministic tests for backend helpers and API contracts.
- If you add tests, place them under `tests/` and name them `test_*.py`.

## Commit & Pull Request Guidelines
- Commit history follows conventional prefixes such as `feat:`, `fix:`, `docs:`, `refactor:`, and `chore:`.
- Keep commits focused and message subjects short and imperative.
- Pull requests should explain the behavior change, list validation performed, and include screenshots for UI updates.

## Security & Configuration Tips
- Keep secrets in `.env`; never commit API keys or tokens.
- Common runtime variables are `MODEL_NAME`, `API_KEY`, and `DEBUG_TRACE`.
- Avoid changing `memory/AGENTS.md` unless you are updating durable agent instructions.
