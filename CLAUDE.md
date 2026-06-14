# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
uv run pytest tests/test_providers.py                  # one test file
uv run pytest tests/test_providers.py::test_fn         # one test
uv run pytest -k name                                    # by name match
uv run ruff check --fix     # lint
uv run black . -l 100       # format
```

`uv run epic-free` honors `ENABLE_APSCHEDULER` from `.env`:

- `false` (the `.env.example` default for local dev) — runs **one** collection
  then exits.
- `true` (the compose default) — runs once immediately, then keeps an
  `AsyncIOScheduler` running recurring crons (Thu 23:30→Fri 03:30 hourly, and
  daily 12:00, both `Asia/Shanghai`) until SIGINT/SIGTERM.

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
- `cleanup.py` — `prune_old_files()`, a fail-soft (never raises) disk-retention
  pruner called once per collection run via `app._prune_old_artifacts()` to bound
  `volumes/` growth (records, debug screenshots, hCaptcha caches).
- `models.py` — pydantic models for Epic order/promotion payloads.
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
- Provider auto-detection: a blank `LLM_PROVIDER` is resolved at validation time
  from the first present key (`openai` → `glm` → `gemini`). Because
  `hcaptcha-challenger` reads `GEMINI_API_KEY` from its base model,
  `EpicSettings` also seeds `GEMINI_API_KEY` from the chosen openai/glm key when
  no Gemini key is set — so a non-Gemini deploy still satisfies the base model.
- The four per-task model fields (`CHALLENGE_CLASSIFIER_MODEL`, etc.) default to
  the chosen provider's default model when left empty; `deploy()` fails fast with
  a clear message via the `epic_configuration_error` / `llm_configuration_error`
  properties rather than letting a missing credential surface as a cryptic
  pydantic error.
- Disk retention is on by default (30 days for both records and runtime
  artifacts); `RECORD_RETENTION_DAYS` / `RUNTIME_RETENTION_DAYS` tune it, `0`
  keeps forever.

## CI / Docker

- `.github/workflows/docker.yml` — builds on push to `main` and publishes
  `ghcr.io/autsunset/epic-free:latest`. The `Dockerfile` + `docker-compose.yaml`
  mount `./volumes/` at `/app/volumes/`.
- `.github/workflows/claim.yml` — a scheduled, fork-guarded claim run for users
  without a server (GitHub-hosted runner; main repo is guarded out, only forks
  with secrets run). The README documents the operator workflow.

## Testing

Tests live in `tests/` and use `pytest` + `pytest-asyncio` (`asyncio_mode = auto`,
so `async def test_*` runs without a decorator). They must pass without network
access. `tests/test_providers.py` covers the LLM parsing/payload-shape path;
`tests/test_cleanup.py` covers the pruner. When adding behavior to the LLM
compatibility layer, add a parsing or payload-shape test alongside it.

## Runtime data

`volumes/` (logs, user_data, runtime, screenshots, record, hcaptcha) is created
at runtime and git-ignored. In Docker it is mounted at `/app/volumes/`.
`cleanup.py` trims it per run (see above).
