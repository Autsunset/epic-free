# -*- coding: utf-8 -*-
"""Deployment entry point: authenticate, claim free games, and optionally schedule recurrences.

APScheduler is the single scheduler (the redundant/broken Celery setup from the
original project was removed). The shared HTTP client is closed on shutdown.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
from contextlib import suppress
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger
from pytz import timezone

from epic_free.browser import open_browser_context
from epic_free.config import LOG_DIR, settings
from epic_free.epic.auth import EpicAuthorization
from epic_free.epic.store import EpicAgent
from epic_free.http_client import close_async_client
from epic_free.logging_setup import init_log

init_log(
    runtime=LOG_DIR.joinpath("runtime.log"),
    error=LOG_DIR.joinpath("error.log"),
)

TIMEZONE = timezone("Asia/Shanghai")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


@logger.catch(reraise=True)
async def execute_browser_tasks(headless: bool = True):
    """Authenticate with Epic and collect this week's free games."""
    logger.debug("Starting Epic Games collection task")

    async with open_browser_context(headless=headless) as browser:
        page = browser.pages[0] if browser.pages else await browser.new_page()
        logger.debug("Browser initialized successfully")

        logger.debug("Initiating Epic Games authentication")
        agent = EpicAuthorization(page)
        is_authenticated = await agent.invoke()
        if not is_authenticated:
            raise RuntimeError("Authentication failed, aborting this run")
        logger.debug("Authentication completed")

        logger.debug("Starting free games collection process")
        game_page = await browser.new_page()
        await EpicAgent(game_page).collect_epic_games()
        logger.debug("Free games collection completed")

        with suppress(Exception):
            for p in browser.pages:
                await p.close()

        logger.debug("Browser tasks execution finished successfully")


async def deploy():
    """Run once immediately, then keep an APScheduler running until signaled."""
    headless = _env_bool("HEADLESS", True)

    logger.debug(
        "Starting deployment with configuration: {}",
        json.dumps(
            {**settings.model_dump(mode="json"), "headless": headless},
            indent=2,
            ensure_ascii=False,
            default=str,
        ),
    )

    if configuration_error := settings.llm_configuration_error:
        logger.error(configuration_error)
        raise RuntimeError(configuration_error)

    # Run once immediately. A partial failure (e.g. one game whose captcha could
    # not be solved) must NOT prevent the scheduler from starting — the scheduler
    # will retry on its next cron tick. @logger.catch already logged the traceback.
    try:
        await execute_browser_tasks(headless=headless)
    except Exception as err:
        logger.error(
            "Immediate collection run failed; the scheduler will retry later | err={!r}",
            err,
        )

    if not settings.ENABLE_APSCHEDULER:
        logger.debug("Scheduler is disabled, deployment completed")
        return

    scheduler = AsyncIOScheduler()

    # Strategy 1: Thursday 23:30 → Friday 03:30, every hour (Beijing Time)
    scheduler.add_job(
        execute_browser_tasks,
        trigger=CronTrigger(
            day_of_week="thu", hour="23,0,1,2,3", minute="30", timezone="Asia/Shanghai"
        ),
        id="weekly_epic_games_task",
        name="weekly_epic_games_task",
        args=[headless],
        replace_existing=False,
        max_instances=1,
    )

    # Strategy 2: Daily at 12:00 PM (Beijing Time)
    scheduler.add_job(
        execute_browser_tasks,
        trigger=CronTrigger(hour="12", minute="0", timezone="Asia/Shanghai"),
        id="daily_epic_games_task",
        name="daily_epic_games_task",
        args=[headless],
        replace_existing=False,
        max_instances=1,
    )

    shutdown_event = asyncio.Event()

    def signal_handler(signum, frame):
        logger.debug(
            f"Received signal {signal.Signals(signum).name}, initiating graceful shutdown"
        )
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    scheduler.start()
    logger.debug("Epic Games scheduler started successfully")
    logger.debug(f"Current time: {datetime.now(TIMEZONE).strftime('%Y-%m-%d %H:%M:%S %Z')}")

    for j in scheduler.get_jobs():
        if next_run := j.next_run_time:
            logger.debug(
                f"Next execution scheduled: {next_run.strftime('%Y-%m-%d %H:%M:%S %Z')} "
                f"(job_id: {j.id})"
            )

    logger.debug("Scheduler is running, send SIGINT or SIGTERM to stop gracefully")
    try:
        await shutdown_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        scheduler.shutdown(wait=True)
        await close_async_client()
        logger.success("Scheduler stopped gracefully")


def main():
    """Console-script entry point."""
    asyncio.run(deploy())


if __name__ == "__main__":
    main()
