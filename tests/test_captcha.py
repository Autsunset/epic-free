"""Challenge-frame readiness without a browser or provider calls."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

import epic_free.captcha as captcha
from epic_free.epic.auth import EpicAuthorization


async def test_frame_replacement_waits_instead_of_returning_none():
    frame = object()
    finder = AsyncMock(side_effect=[None, frame])
    page = SimpleNamespace(wait_for_timeout=AsyncMock())
    agent = SimpleNamespace(
        page=page, robotic_arm=SimpleNamespace(get_challenge_frame_locator=finder)
    )

    assert captcha.prepare_challenge_agent(agent) is agent
    assert await agent.robotic_arm.get_challenge_frame_locator() is frame

    assert finder.await_count == 2
    page.wait_for_timeout.assert_awaited_once_with(200)


async def test_missing_challenge_frame_has_a_bounded_wait(monkeypatch):
    finder = AsyncMock(return_value=None)
    agent = SimpleNamespace(
        page=SimpleNamespace(wait_for_timeout=AsyncMock()),
        robotic_arm=SimpleNamespace(get_challenge_frame_locator=finder),
    )
    ticks = iter([0, 0, 16])
    monkeypatch.setattr(captcha, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    captcha.prepare_challenge_agent(agent)

    with pytest.raises(PlaywrightTimeoutError, match="did not become ready"):
        await agent.robotic_arm.get_challenge_frame_locator()


async def test_frame_transport_errors_are_not_swallowed():
    agent = SimpleNamespace(
        page=SimpleNamespace(wait_for_timeout=AsyncMock()),
        robotic_arm=SimpleNamespace(
            get_challenge_frame_locator=AsyncMock(side_effect=RuntimeError("browser closed"))
        ),
    )
    captcha.prepare_challenge_agent(agent)

    with pytest.raises(RuntimeError, match="browser closed"):
        await agent.robotic_arm.get_challenge_frame_locator()


def frame(kind, *, ready=True, visible=True):
    return SimpleNamespace(
        url=f"https://newassets.hcaptcha.com/captcha/v1/test/static/hcaptcha.html#frame={kind}",
        locator=lambda selector: SimpleNamespace(is_visible=AsyncMock(return_value=ready)),
        frame_element=AsyncMock(
            return_value=SimpleNamespace(evaluate=AsyncMock(return_value=visible))
        ),
    )


async def test_checkbox_is_not_an_active_image_challenge():
    authorization = EpicAuthorization(SimpleNamespace(frames=[frame("checkbox")]))
    authorization._page_body_text = AsyncMock(return_value="One more step: verify you are human")

    assert await authorization._has_visible_hcaptcha() is False


async def test_challenge_iframe_must_finish_rendering():
    authorization = EpicAuthorization(SimpleNamespace(frames=[frame("challenge", ready=False)]))

    assert await authorization._has_visible_hcaptcha() is False


async def test_ready_challenge_view_is_detected():
    authorization = EpicAuthorization(SimpleNamespace(frames=[frame("challenge")]))

    assert await authorization._has_visible_hcaptcha() is True


async def test_hidden_challenge_is_not_solved_again():
    authorization = EpicAuthorization(SimpleNamespace(frames=[frame("challenge", visible=False)]))

    assert await authorization._has_visible_hcaptcha() is False
