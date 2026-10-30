"""Login-form regressions without contacting Epic or an LLM provider."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from pydantic import SecretStr

import epic_free.epic.auth as auth_module
from epic_free.epic.auth import EpicAuthorization, EpicManualActionRequiredError


class LoginControl:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector
        self.enabled = True

    async def is_visible(self):
        if self.selector == "#password":
            return self.page.password_visible
        if self.selector == "#sign-in":
            return not self.page.combined and self.page.password_ready
        if self.selector == "#continue":
            return self.page.combined or not self.page.password_ready
        return True

    async def evaluate(self, script, **kwargs):
        return self.page.password_ready

    async def is_enabled(self):
        return self.enabled

    async def fill(self, value):
        assert await self.is_visible()
        self.page.filled.add(self.selector)
        self.page.events.append(("fill", self.selector))

    async def click(self, **kwargs):
        assert await self.is_visible()
        assert self.enabled
        self.page.events.append(("click", self.selector))
        if self.selector == "#continue" and not self.page.password_ready:
            if self.page.email_challenge:
                self.page.challenge_visible = True
            else:
                self.page.password_visible = self.page.password_ready = True
        else:
            assert self.page.filled == {"#email", "#password"}
            self.page.final_submitted = True
            self.page.challenge_visible = self.page.require_challenge
            self.page.logged_in = not self.page.require_challenge


class LoginPage:
    def __init__(self, *, combined=True):
        self.combined = combined
        self.password_visible = combined
        self.password_ready = combined
        self.challenge_visible = False
        self.email_challenge = False
        self.require_challenge = True
        self.final_submitted = False
        self.logged_in = False
        self.events = []
        self.filled = set()
        self.controls = {}
        self.url = "https://www.epicgames.com/id/login"
        self.goto = AsyncMock()
        self.wait_for_timeout = AsyncMock()
        self.screenshot = AsyncMock()

    def locator(self, selector):
        return self.controls.setdefault(selector, LoginControl(self, selector))

    async def click(self, selector, **kwargs):
        await self.locator(selector).click(**kwargs)


def make_login(monkeypatch, tmp_path, *, combined=True):
    page = LoginPage(combined=combined)
    authorization = EpicAuthorization(page)
    agent = SimpleNamespace(
        page=page,
        robotic_arm=SimpleNamespace(get_challenge_frame_locator=AsyncMock()),
        wait_for_challenge=AsyncMock(),
    )

    async def solve():
        page.events.append(("solve", "captcha"))
        page.challenge_visible = False
        if page.final_submitted:
            page.logged_in = True
        elif not page.password_ready:
            page.password_visible = page.password_ready = True

    async def login_outcome(*args, **kwargs):
        if not page.logged_in:
            raise PlaywrightTimeoutError("not yet logged in")

    agent.wait_for_challenge.side_effect = solve
    monkeypatch.setattr(auth_module, "AgentV", lambda **kwargs: agent)
    monkeypatch.setattr(
        auth_module,
        "settings",
        SimpleNamespace(EPIC_EMAIL="test@example.invalid", EPIC_PASSWORD=SecretStr("fake")),
    )
    monkeypatch.setattr(auth_module, "SCREENSHOTS_DIR", tmp_path)

    def expect_control(control):
        async def visible(**kwargs):
            assert await control.is_visible()

        return SimpleNamespace(to_be_visible=visible)

    monkeypatch.setattr(auth_module, "expect", expect_control)
    authorization._wait_for_login_form = AsyncMock()
    authorization._has_visible_hcaptcha = AsyncMock(side_effect=lambda: page.challenge_visible)
    authorization._await_login_outcome = AsyncMock(side_effect=login_outcome)
    authorization._handle_right_account_validation = AsyncMock()
    authorization._establish_store_session = AsyncMock()
    authorization._goto_claim_page = AsyncMock()
    authorization._ensure_store_session_ready = AsyncMock()
    return authorization, page, agent


async def test_combined_form_fills_both_fields_before_continue(monkeypatch, tmp_path):
    authorization, page, agent = make_login(monkeypatch, tmp_path)

    assert await authorization._login() is True

    assert page.events == [
        ("fill", "#email"),
        ("fill", "#password"),
        ("click", "#continue"),
        ("solve", "captcha"),
    ]
    agent.wait_for_challenge.assert_awaited_once()
    authorization._ensure_store_session_ready.assert_awaited_once()


async def test_legacy_email_then_password_form_still_works(monkeypatch, tmp_path):
    authorization, page, agent = make_login(monkeypatch, tmp_path, combined=False)

    assert await authorization._login() is True

    assert page.events == [
        ("fill", "#email"),
        ("click", "#continue"),
        ("fill", "#password"),
        ("click", "#sign-in"),
        ("solve", "captcha"),
    ]
    agent.wait_for_challenge.assert_awaited_once()


async def test_existing_challenge_is_solved_before_resuming_submit(monkeypatch, tmp_path):
    authorization, page, agent = make_login(monkeypatch, tmp_path)
    page.challenge_visible = True

    assert await authorization._login() is True

    assert page.events == [
        ("fill", "#email"),
        ("fill", "#password"),
        ("solve", "captcha"),
        ("click", "#continue"),
        ("solve", "captcha"),
    ]
    assert agent.wait_for_challenge.await_count == 2


async def test_offscreen_autofill_field_is_not_treated_as_a_password_step(monkeypatch, tmp_path):
    authorization, page, _ = make_login(monkeypatch, tmp_path, combined=False)
    page.password_visible = True

    assert await authorization._login() is True

    assert page.events[:3] == [
        ("fill", "#email"),
        ("click", "#continue"),
        ("fill", "#password"),
    ]


async def test_email_step_challenge_is_solved_before_filling_real_password(monkeypatch, tmp_path):
    authorization, page, agent = make_login(monkeypatch, tmp_path, combined=False)
    page.password_visible = True
    page.email_challenge = True

    assert await authorization._login() is True

    assert page.events == [
        ("fill", "#email"),
        ("click", "#continue"),
        ("solve", "captcha"),
        ("fill", "#password"),
        ("click", "#sign-in"),
        ("solve", "captcha"),
    ]
    assert agent.wait_for_challenge.await_count == 2


async def test_no_challenge_does_not_wait_for_solver(monkeypatch, tmp_path):
    authorization, page, agent = make_login(monkeypatch, tmp_path)
    page.require_challenge = False

    assert await authorization._login() is True

    agent.wait_for_challenge.assert_not_awaited()


async def test_email_challenge_retries_are_bounded(monkeypatch, tmp_path):
    authorization, page, agent = make_login(monkeypatch, tmp_path, combined=False)
    page.challenge_visible = True
    agent.wait_for_challenge.side_effect = None

    with pytest.raises(PlaywrightTimeoutError, match="Email-step challenge"):
        await authorization._wait_for_password_form(agent, submitted=True)

    assert agent.wait_for_challenge.await_count == 3


async def test_password_form_wait_times_out_without_a_challenge(monkeypatch, tmp_path):
    authorization, _, agent = make_login(monkeypatch, tmp_path, combined=False)
    ticks = iter([0, 0, 46])
    monkeypatch.setattr(auth_module, "time", SimpleNamespace(monotonic=lambda: next(ticks)))

    with pytest.raises(PlaywrightTimeoutError, match="real Epic password form"):
        await authorization._wait_for_password_form(agent, submitted=True)

    agent.wait_for_challenge.assert_not_awaited()


async def test_rejected_email_verification_stops_before_another_solve(monkeypatch, tmp_path):
    authorization, _, agent = make_login(monkeypatch, tmp_path, combined=False)
    authorization._page_body_text = AsyncMock(
        return_value="Incorrect response. Please refresh the page."
    )

    with pytest.raises(EpicManualActionRequiredError, match="Epic rejected the verification"):
        await authorization._wait_for_password_form(agent, submitted=True)

    agent.wait_for_challenge.assert_not_awaited()


async def test_rejected_verification_is_not_misreported_as_outcome_timeout(monkeypatch, tmp_path):
    authorization, _, _ = make_login(monkeypatch, tmp_path)
    authorization._page_body_text = AsyncMock(
        return_value="Incorrect response. Please refresh the page."
    )

    with pytest.raises(EpicManualActionRequiredError, match="not a confirmed login"):
        await EpicAuthorization._await_login_outcome(authorization, "https://www.epicgames.com")


async def test_manual_verification_rejection_does_not_retry_login_three_times(
    monkeypatch, tmp_path
):
    authorization, page, _ = make_login(monkeypatch, tmp_path)
    page.on = lambda *args: None
    authorization._get_login_status = AsyncMock(return_value="false")
    authorization._login = AsyncMock(
        side_effect=EpicManualActionRequiredError("verification rejected")
    )

    with pytest.raises(EpicManualActionRequiredError, match="verification rejected"):
        await authorization.invoke()

    authorization._login.assert_awaited_once()


async def test_challenge_appearing_during_click_is_not_a_login_timeout(monkeypatch, tmp_path):
    authorization, page, _ = make_login(monkeypatch, tmp_path)
    authorization._has_visible_hcaptcha.side_effect = [False, True]
    button = page.locator("#continue")
    button.click = AsyncMock(side_effect=PlaywrightTimeoutError("challenge intercepted click"))

    await authorization._submit_login()

    button.click.assert_awaited_once()
    assert button.click.await_args.kwargs["timeout"] <= 1000


async def test_sign_in_button_is_preferred_when_both_controls_exist(monkeypatch, tmp_path):
    authorization, page, _ = make_login(monkeypatch, tmp_path)
    page.filled = {"#email", "#password"}
    page.locator("#sign-in").is_visible = AsyncMock(return_value=True)

    await authorization._submit_login()

    assert page.events == [("click", "#sign-in")]


async def test_disabled_submit_times_out_without_clicking(monkeypatch, tmp_path):
    authorization, page, _ = make_login(monkeypatch, tmp_path)
    button = page.locator("#continue")
    button.enabled = False
    button.click = AsyncMock()
    ticks = iter([0, 0, 31])
    monkeypatch.setattr(auth_module, "time", SimpleNamespace(monotonic=lambda: next(ticks)))

    with pytest.raises(PlaywrightTimeoutError, match="login submit"):
        await authorization._submit_login()

    button.click.assert_not_awaited()


async def test_submit_transport_errors_are_not_hidden(monkeypatch, tmp_path):
    authorization, page, _ = make_login(monkeypatch, tmp_path)
    page.locator("#continue").click = AsyncMock(side_effect=RuntimeError("browser closed"))

    with pytest.raises(RuntimeError, match="browser closed"):
        await authorization._submit_login()


async def test_unverified_login_does_not_report_success(monkeypatch, tmp_path):
    authorization, page, _ = make_login(monkeypatch, tmp_path)
    authorization._await_login_outcome.side_effect = PlaywrightTimeoutError("not logged in")

    assert await authorization._login() is None

    page.screenshot.assert_awaited_once()
    authorization._ensure_store_session_ready.assert_not_awaited()


def make_account_validation(monkeypatch, *, prompt=None):
    controls = {
        selector: SimpleNamespace(visible=selector == prompt, click=AsyncMock())
        for selector in ("#link-success", "#login-reminder-prompt-setup-tfa-skip", "#yes")
    }

    async def tick(milliseconds):
        await asyncio.sleep(0)

    page = SimpleNamespace(
        url="https://www.epicgames.com/account/personal",
        goto=AsyncMock(),
        wait_for_timeout=tick,
        locator=lambda selector: controls[selector],
    )

    def expect_control(control):
        async def visible(**kwargs):
            if not control.visible:
                raise PlaywrightTimeoutError("prompt absent")

        return SimpleNamespace(to_be_visible=visible)

    monkeypatch.setattr(auth_module, "expect", expect_control)
    return EpicAuthorization(page), controls


async def test_account_page_without_legacy_prompts_does_not_wait_for_old_csrf_signal(monkeypatch):
    authorization, controls = make_account_validation(monkeypatch)

    await asyncio.wait_for(authorization._handle_right_account_validation(), timeout=0.1)

    for control in controls.values():
        control.click.assert_not_awaited()


async def test_account_confirmation_prompt_is_handled_then_validation_returns(monkeypatch):
    authorization, controls = make_account_validation(monkeypatch, prompt="#yes")

    async def confirm(**kwargs):
        controls["#yes"].visible = False

    controls["#yes"].click.side_effect = confirm

    await asyncio.wait_for(authorization._handle_right_account_validation(), timeout=0.1)

    controls["#yes"].click.assert_awaited_once()
    controls["#link-success"].click.assert_not_awaited()


async def test_store_session_can_finish_hydrating_after_initial_false(monkeypatch, tmp_path):
    authorization, page, _ = make_login(monkeypatch, tmp_path)
    authorization._get_login_status = AsyncMock(side_effect=["false", "true"])

    await EpicAuthorization._ensure_store_session_ready(authorization)

    assert authorization._get_login_status.await_count == 2
    page.wait_for_timeout.assert_awaited_once_with(500)


async def test_store_session_still_false_at_deadline_is_not_accepted(monkeypatch, tmp_path):
    authorization, _, _ = make_login(monkeypatch, tmp_path)
    authorization._get_login_status = AsyncMock(return_value="false")
    ticks = iter([0, 0, 46])
    monkeypatch.setattr(auth_module, "time", SimpleNamespace(monotonic=lambda: next(ticks)))

    with pytest.raises(RuntimeError, match="isloggedin=false"):
        await EpicAuthorization._ensure_store_session_ready(authorization)


@pytest.mark.parametrize(
    ("label", "email", "matches"),
    [
        ("Name\n t***t@example.invalid", "test@example.invalid", True),
        ("Name\nTEST@EXAMPLE.INVALID", "test@example.invalid", True),
        ("Name\n t…t@example.invalid", "test@example.invalid", True),
        ("Name\n t***x@example.invalid", "test@example.invalid", False),
        ("Name\n t***t@other.invalid", "test@example.invalid", False),
        ("Name\n another@example.invalid", "test@example.invalid", False),
        ("Name without an email", "test@example.invalid", False),
        ("t***t@example.invalid", "invalid-email", False),
    ],
)
def test_saved_account_hint_matching(label, email, matches):
    assert EpicAuthorization._account_hint_matches(label, email) is matches


async def test_saved_account_chooser_selects_only_the_configured_email(monkeypatch, tmp_path):
    authorization, page, _ = make_login(monkeypatch, tmp_path)
    other = SimpleNamespace(
        is_visible=AsyncMock(return_value=True),
        inner_text=AsyncMock(return_value="Other\n o***r@other.invalid"),
        click=AsyncMock(),
    )
    intended = SimpleNamespace(
        is_visible=AsyncMock(return_value=True),
        inner_text=AsyncMock(return_value="Account\n t***t@example.invalid"),
        click=AsyncMock(),
    )
    page.locator = lambda selector: SimpleNamespace(all=AsyncMock(return_value=[other, intended]))

    assert await EpicAuthorization._select_saved_account(authorization) is True

    intended.click.assert_awaited_once()
    other.click.assert_not_awaited()


async def test_ambiguous_saved_accounts_require_manual_selection(monkeypatch, tmp_path):
    authorization, page, _ = make_login(monkeypatch, tmp_path)
    card = SimpleNamespace(
        is_visible=AsyncMock(return_value=True),
        inner_text=AsyncMock(return_value="t***t@example.invalid"),
        click=AsyncMock(),
    )
    page.locator = lambda selector: SimpleNamespace(all=AsyncMock(return_value=[card, card]))

    with pytest.raises(EpicManualActionRequiredError, match="uniquely match"):
        await EpicAuthorization._select_saved_account(authorization)

    card.click.assert_not_awaited()


async def test_store_sso_waits_for_actual_store_login(monkeypatch, tmp_path):
    authorization, page, agent = make_login(monkeypatch, tmp_path)
    page.url = "https://store.epicgames.com/"
    authorization._get_login_status = AsyncMock(return_value="true")
    authorization._select_saved_account = AsyncMock()

    await EpicAuthorization._establish_store_session(authorization, agent)

    page.goto.assert_awaited_with(
        auth_module.URL_STORE_LOGIN, wait_until="domcontentloaded", timeout=45000
    )
    authorization._select_saved_account.assert_not_awaited()


async def test_store_sso_completes_saved_account_handoff(monkeypatch, tmp_path):
    authorization, page, agent = make_login(monkeypatch, tmp_path)
    page.url = "https://www.epicgames.com/id/login/switch-account"
    authorization._get_login_status = AsyncMock(return_value="true")

    async def select():
        page.url = "https://store.epicgames.com/free-games"
        return True

    authorization._select_saved_account = AsyncMock(side_effect=select)

    await EpicAuthorization._establish_store_session(authorization, agent)

    authorization._select_saved_account.assert_awaited_once()
    authorization._get_login_status.assert_awaited_once()


async def test_store_sso_timeout_does_not_accept_a_portal_only_session(monkeypatch, tmp_path):
    authorization, page, agent = make_login(monkeypatch, tmp_path)
    page.url = "https://store.epicgames.com/"
    authorization._get_login_status = AsyncMock(return_value="false")
    ticks = iter([0, 0, 46])
    monkeypatch.setattr(auth_module, "time", SimpleNamespace(monotonic=lambda: next(ticks)))

    with pytest.raises(PlaywrightTimeoutError, match="store SSO session"):
        await EpicAuthorization._establish_store_session(authorization, agent)
