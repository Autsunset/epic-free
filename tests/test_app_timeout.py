# -*- coding: utf-8 -*-
"""Tests for the TASK_TIMEOUT_SECONDS wrapper in app.py.

A hung collection run must not propagate (the scheduler keeps going); a 0/None
timeout must run the task to completion uncapped.
"""

import asyncio
from types import SimpleNamespace

import epic_free.app as app


async def test_run_task_with_timeout_aborts_hung_run(monkeypatch):
    started = {}

    async def _hang(headless=True):
        started["ran"] = True
        await asyncio.sleep(10)

    monkeypatch.setattr(app, "execute_browser_tasks", _hang)
    monkeypatch.setattr(app, "settings", SimpleNamespace(TASK_TIMEOUT_SECONDS=0.05))

    # Must return normally: the TimeoutError is caught and logged, not raised.
    await app._run_task_with_timeout(headless=True)
    assert started["ran"] is True


async def test_run_task_with_timeout_disabled_runs_to_completion(monkeypatch):
    seen = {}

    async def _quick(headless=True):
        seen["headless"] = headless

    monkeypatch.setattr(app, "execute_browser_tasks", _quick)
    monkeypatch.setattr(app, "settings", SimpleNamespace(TASK_TIMEOUT_SECONDS=0))

    await app._run_task_with_timeout(headless=False)
    assert seen["headless"] is False
