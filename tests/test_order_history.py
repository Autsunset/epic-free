import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from epic_free.epic.auth import EpicManualActionRequiredError
from epic_free.epic.store import EpicAgent, EpicGames, _fetch_order_history
from epic_free.models import PromotionGame

HISTORY_URL = "https://accounts.epicgames.com/account/v2/payment/ajaxGetOrderHistory"
SWITCH_URL = "https://www.epicgames.com/id/login/switch-account"
NAMESPACE = "a" * 32
PAYLOAD = {
    "orders": [
        {
            "orderType": "PURCHASE",
            "orderId": "order-1",
            "items": [{"description": "Game", "offerId": "offer-1", "namespace": NAMESPACE}],
        }
    ],
    "nextPageToken": "more",
}


def make_page(url, payload=PAYLOAD):
    pre = SimpleNamespace(
        first=SimpleNamespace(text_content=AsyncMock(return_value=json.dumps(payload)))
    )
    card = SimpleNamespace(
        is_visible=AsyncMock(return_value=True),
        inner_text=AsyncMock(return_value="person@example.com"),
        click=AsyncMock(),
    )
    cards = SimpleNamespace(
        first=SimpleNamespace(wait_for=AsyncMock()),
        all=AsyncMock(return_value=[card]),
    )
    page = SimpleNamespace(
        url=url,
        goto=AsyncMock(),
        wait_for_function=AsyncMock(),
        locator=lambda selector: pre if selector == "pre" else cards,
    )

    async def complete_handoff(*args, **kwargs):
        page.url = HISTORY_URL

    page.wait_for_function.side_effect = complete_handoff
    return page, card, pre


async def test_order_history_handles_saved_account_handoff(monkeypatch):
    from epic_free.epic import auth

    monkeypatch.setattr(auth.settings, "EPIC_EMAIL", "person@example.com")
    page, card, _ = make_page(SWITCH_URL)
    assert await _fetch_order_history(page) == PAYLOAD
    card.click.assert_awaited_once()
    page.wait_for_function.assert_awaited_once()


async def test_order_history_does_not_choose_unmatched_account(monkeypatch):
    from epic_free.epic import auth

    monkeypatch.setattr(auth.settings, "EPIC_EMAIL", "someone-else@example.com")
    page, card, _ = make_page(SWITCH_URL)
    with pytest.raises(EpicManualActionRequiredError):
        await _fetch_order_history(page)
    card.click.assert_not_awaited()


async def test_order_history_waits_for_login_redirect(monkeypatch):
    from epic_free.epic import auth

    monkeypatch.setattr(auth.settings, "EPIC_EMAIL", "person@example.com")
    page, card, _ = make_page("https://www.epicgames.com/id/login")

    async def transition(*args, **kwargs):
        if page.url.endswith("/id/login"):
            page.url = SWITCH_URL
        else:
            page.url = HISTORY_URL

    page.wait_for_function.side_effect = transition
    assert await _fetch_order_history(page) == PAYLOAD
    card.click.assert_awaited_once()
    assert page.wait_for_function.await_count == 2


async def test_order_history_rejects_redirect_without_waiting_for_json():
    page, _, pre = make_page("https://www.epicgames.com/id/login/mfa")
    with pytest.raises(RuntimeError, match="unexpected page"):
        await _fetch_order_history(page)
    pre.first.text_content.assert_not_awaited()


async def test_order_history_rejects_invalid_payload():
    page, _, _ = make_page(HISTORY_URL, {"error": "unauthorized"})
    with pytest.raises(ValueError, match="invalid response"):
        await _fetch_order_history(page)


async def test_history_used_for_initial_filter_and_checkout_verification(monkeypatch):
    from epic_free.epic import store

    fetch = AsyncMock(return_value=PAYLOAD)
    monkeypatch.setattr(store, "_fetch_order_history", fetch)
    page = SimpleNamespace()
    agent = EpicAgent(page)
    await agent._sync_order_history()
    assert [item.namespace for item in agent._orders] == [NAMESPACE]

    game = PromotionGame(
        title="Game",
        id="offer-1",
        namespace=NAMESPACE,
        description="",
        offerType="BASE_GAME",
        url="https://store.epicgames.com/en-US/p/game",
    )
    assert await EpicGames(page)._is_promotion_in_order_history(game)
    assert fetch.await_count == 2
