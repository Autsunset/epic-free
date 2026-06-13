# CLAUDE.md

Guidance for Claude Code (and other coding agents) working in this repository.

## Project Overview

`epic-free` claims weekly free games from the Epic Games Store via browser
automation (Playwright / Camoufox) and an AI-powered hCaptcha solver
(`hcaptcha-challenger`). It is a refactor of `epic-freebies-helper` that merges
the previously-separate OpenAI-compatible provider branch into the mainline.

## Development Commands

```bash
uv sync                     # install (editable) + create .venv
uv run camoufox fetch       # download the Camoufox browser binary
uv run epic-free            # run the app (reads .env)
uv run pytest               # run tests
uv run ruff check --fix     # lint
uv run black . -l 100       # format
```

## Architecture

Standard `src/` layout; the package is `epic_free`. Entry point is the
`epic-free` console script → `epic_free.app:main`.

- `config.py` — `EpicSettings` (extends `hcaptcha_challenger.agent.AgentConfig`).
  Holds the merged `gemini` / `glm` / `openai` provider config and applies the
  provider patch at import time.
- `http_client.py` — shared pooled `httpx.AsyncClient`. **All** outbound HTTP
  (LLM calls + promotions fetch) goes through it; do not open a per-call client.
- `llm/`
  - `parse.py` — response-normalization helpers shared by glm/openai (ported,
    test-covered). Treat as stable.
  - `openai_compat.py` — `OpenAICompatibleClient`, the single client serving
    both `glm` and `openai` (impersonates `google.genai.Client`).
  - `gemini.py` — native GenAI SDK patch (key pin + optional relay base URL).
  - `patch.py` — dispatches `apply_llm_patch` based on `LLM_PROVIDER`.
- `epic/`
  - `auth.py` — `EpicAuthorization` (login + session validation). **Ported
    verbatim from upstream master — do not rewrite**, the resilience is
    battle-tested.
  - `store.py` — `EpicAgent` / `EpicGames` (claim flow). Same: ported, keep.
  - `promotions.py` — async `get_promotions()` (was a blocking sync call).
- `browser.py` — `open_browser_context` (Camoufox → Playwright Firefox fallback).
- `app.py` — `deploy()` / `execute_browser_tasks()`; APScheduler only.
- `logging_setup.py` — loguru sinks (Shanghai timezone filter).

## Key design decisions (do not casually change)

- `glm` and `openai` are the same wire format → one client, dispatched by
  `settings.LLM_PROVIDER`. The only behavioral difference is image encoding
  (`data_url_images`: OpenAI=True, GLM=False).
- The `google.genai` monkey-patch is required because `hcaptcha-challenger`
  calls `genai.Client().aio.models.generate_content(...)` internally. Keep it.
- Celery was removed (its task import path was already broken upstream).
  APScheduler is the only scheduler.

## Testing

Tests live in `tests/` and use `pytest` + `pytest-asyncio`. They must pass
without network access. When adding behavior to the LLM compatibility layer,
add a parsing or payload-shape test alongside it.

## Runtime data

`volumes/` (logs, user_data, runtime, screenshots, record, hcaptcha) is created
at runtime and git-ignored. In Docker it is mounted at `/app/volumes/`.
