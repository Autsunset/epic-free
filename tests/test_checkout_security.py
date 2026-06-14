# -*- coding: utf-8 -*-
"""Tests for the tightened checkout security-check resolver and crash-tolerant
instant-checkout recovery.

Covers two production failure modes:

* ``_resolve_checkout_security_check`` used to misreport ``solved successfully`` when
  ``wait_for_challenge()`` *timed out* (the dialog then closed), leaving Place Order
  spinning on a game that was never claimed. It must now treat ``_is_claimed_state``
  as the only unconditional success signal and bail out fast on repeated solve failures.
* ``_handle_instant_checkout``'s ``except`` block used to call ``_capture_purchase_debug``
  (and other page-touching recovery steps) without protection; once the browser/driver
  was already dead ("Connection closed while reading from the driver") the secondary
  crash propagated and aborted the whole collection run. Recovery is now contained.
"""

# hcaptcha-challenger's AgentConfig marks GEMINI_API_KEY required; config.py
# instantiates settings at import time. Seed placeholder creds so importing the
# store module works in a network-less test environment (production seeds
# GEMINI_API_KEY from the openai/glm key via _bridge_provider_credentials).
import os

os.environ.setdefault("LLM_PROVIDER", "openai")
os.environ.setdefault("OPENAI_API_KEY", "fake-test-key")
os.environ.setdefault("GEMINI_API_KEY", "fake-test-key")

from types import SimpleNamespace
from unittest.mock import AsyncMock

from epic_free.epic.store import EpicGames

URL = "https://store.epicgames.com/en-US/p/some-game"


def _make_page():
    return SimpleNamespace(wait_for_timeout=AsyncMock(), url=URL)


# ---------------------------------------------------------------------------
# _resolve_checkout_security_check
# ---------------------------------------------------------------------------


class _FakeEpic:
    """Stand-in exposing only the helpers the resolver calls on ``self``."""

    def __init__(self, *, visible_fn, claimed_fn, outcome_fn):
        self._visible_fn = visible_fn
        self._claimed_fn = claimed_fn
        self._outcome_fn = outcome_fn
        self.captures = []

    async def _is_checkout_security_check_visible(self, page):
        return self._visible_fn()

    async def _is_claimed_state(self, page, url):
        return self._claimed_fn()

    async def _observe_checkout_outcome(self, page, url, timeout_ms):
        return self._outcome_fn()

    async def _capture_purchase_debug(self, page, reason, url):
        self.captures.append(reason)


async def test_claimed_state_is_unconditional_success():
    fake = _FakeEpic(
        visible_fn=lambda: True,
        claimed_fn=lambda: True,
        outcome_fn=lambda: "security",
    )
    result = await EpicGames._resolve_checkout_security_check(
        fake, _make_page(), SimpleNamespace(wait_for_challenge=AsyncMock()), URL
    )
    assert result is True


async def test_clean_solve_then_dialog_vanish_is_success():
    """Normal residential-IP path: solver returns cleanly, dialog then disappears."""
    state = {"solved": False}

    async def wait_for_challenge():
        state["solved"] = True

    agent = SimpleNamespace(wait_for_challenge=wait_for_challenge)
    fake = _FakeEpic(
        visible_fn=lambda: not state["solved"],
        claimed_fn=lambda: False,
        outcome_fn=lambda: "checkout",
    )
    result = await EpicGames._resolve_checkout_security_check(fake, _make_page(), agent, URL)
    assert result is True


async def test_clean_solve_into_checkout_outcome_is_success():
    """A clean solve followed by outcome=='checkout' still clears (guard did not over-tighten)."""
    state = {"solved": False}

    async def wait_for_challenge():
        state["solved"] = True

    agent = SimpleNamespace(wait_for_challenge=wait_for_challenge)
    fake = _FakeEpic(
        visible_fn=lambda: True,  # dialog stays visible
        claimed_fn=lambda: False,
        outcome_fn=lambda: "checkout",
    )
    result = await EpicGames._resolve_checkout_security_check(fake, _make_page(), agent, URL)
    assert result is True


async def test_consecutive_failures_aborts_instead_of_looping():
    """Solver keeps failing (datacenter payload timeout) -> give up fast, return False."""

    async def wait_for_challenge():
        raise TimeoutError("Wait for captcha payload to timeout")

    agent = SimpleNamespace(wait_for_challenge=wait_for_challenge)
    fake = _FakeEpic(
        visible_fn=lambda: True,
        claimed_fn=lambda: False,
        outcome_fn=lambda: "security",
    )
    result = await EpicGames._resolve_checkout_security_check(
        fake, _make_page(), agent, URL, max_consecutive_failures=2
    )
    assert result is False
    assert "checkout_security_check_unresolved" in fake.captures


async def test_timeout_dialog_vanish_is_not_misreported_as_success():
    """Regression for the production bug: a timed-out solve closes the dialog and the
    page reports 'back to checkout', but the game was never claimed -> must NOT succeed."""
    state = {"failed": False}

    async def wait_for_challenge():
        state["failed"] = True  # dialog vanishes on the way out
        raise TimeoutError("Challenge execution timed out")

    agent = SimpleNamespace(wait_for_challenge=wait_for_challenge)
    fake = _FakeEpic(
        visible_fn=lambda: not state["failed"],
        claimed_fn=lambda: False,
        outcome_fn=lambda: "checkout",  # old code treated this as "cleared back to checkout"
    )
    result = await EpicGames._resolve_checkout_security_check(
        fake, _make_page(), agent, URL, max_consecutive_failures=2
    )
    assert result is False


# ---------------------------------------------------------------------------
# _recover_after_checkout_error (browser-crash containment)
# ---------------------------------------------------------------------------


class _FakeRecovery:
    """Stand-in for the page-touching helpers the recovery path calls on ``self``."""

    def __init__(
        self,
        *,
        capture_raises=False,
        device_raises=False,
        claimed=False,
        claimed_raises=False,
        finalize=None,
        finalize_raises=False,
    ):
        self.capture_raises = capture_raises
        self.device_raises = device_raises
        self.claimed = claimed
        self.claimed_raises = claimed_raises
        self._finalize = finalize
        self.finalize_raises = finalize_raises
        self.captures = []

    async def _capture_purchase_debug(self, page, reason, url):
        self.captures.append(reason)
        if self.capture_raises:
            raise RuntimeError("Connection closed while reading from the driver")

    async def _handle_device_not_supported_modal(self, page, url, timeout_ms=5000):
        if self.device_raises:
            raise RuntimeError("Connection closed while reading from the driver")

    async def _is_claimed_state(self, page, url):
        if self.claimed_raises:
            raise RuntimeError("Connection closed while reading from the driver")
        return self.claimed

    async def _finalize_unconfirmed_checkout(self, page, promotion):
        if self.finalize_raises:
            raise RuntimeError("Connection closed while reading from the driver")
        return bool(self._finalize)


_PROMO = SimpleNamespace(url=URL)


async def test_recovery_returns_false_when_browser_dead():
    """Every recovery step raises (driver dead) -> return False, never propagate."""
    fake = _FakeRecovery(capture_raises=True, device_raises=True)
    result = await EpicGames._recover_after_checkout_error(fake, _make_page(), _PROMO, URL)
    assert result is False


async def test_recovery_returns_true_when_already_claimed():
    """Capture fails but the game is already claimed -> still a success."""
    fake = _FakeRecovery(capture_raises=True, claimed=True)
    result = await EpicGames._recover_after_checkout_error(fake, _make_page(), _PROMO, URL)
    assert result is True


async def test_recovery_falls_through_to_finalize():
    fake = _FakeRecovery(claimed=False, finalize=True)
    result = await EpicGames._recover_after_checkout_error(fake, _make_page(), _PROMO, URL)
    assert result is True
