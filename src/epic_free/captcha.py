"""Compatibility guard for challenge-frame replacement in hcaptcha-challenger."""

from __future__ import annotations

import time

from hcaptcha_challenger.agent import AgentV
from playwright.async_api import TimeoutError as PlaywrightTimeoutError


def prepare_challenge_agent(agent: AgentV, timeout_seconds: float = 15) -> AgentV:
    find_frame = agent.robotic_arm.get_challenge_frame_locator

    async def ready_frame():
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            frame = await find_frame()
            if frame is not None:
                return frame
            await agent.page.wait_for_timeout(200)
        raise PlaywrightTimeoutError("Challenge frame did not become ready")

    # Version 0.19 captures the frame before its render wait and otherwise retries
    # the same None frame after a challenge is replaced.
    agent.robotic_arm.get_challenge_frame_locator = ready_frame
    return agent
