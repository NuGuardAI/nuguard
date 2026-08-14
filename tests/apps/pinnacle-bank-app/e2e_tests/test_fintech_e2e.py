"""
Pinnacle Bank - End-to-End UI Test Suite
=========================================
Tests all major features of the Fintech App via the Playwright browser
automation library against the running frontend at http://localhost:8080.

Usage:
    pip install playwright pytest-playwright
    python -m playwright install chromium
    pytest e2e_tests/test_fintech_e2e.py -v --timeout=120

Environment (optional overrides):
    FRONTEND_URL      - defaults to http://localhost:8080
    ORCHESTRATOR_URL  - defaults to http://localhost:8001
    APP_USERNAME      - defaults to alice.johnson@pinnaclebank.com
    APP_PASSWORD      - defaults to demo123

All timeouts are generous to account for LLM response latency.
This test file is fully portable: no hard-coded OS paths.
"""

from __future__ import annotations

import os
import re
import time

import pytest
from playwright.sync_api import Page, expect

# Configuration
FRONTEND_URL: str = os.environ.get("FRONTEND_URL", "http://localhost:8080")
ORCHESTRATOR_URL: str = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8001")
USERNAME: str = os.environ.get("APP_USERNAME", "alice.johnson@pinnaclebank.com")
PASSWORD: str = os.environ.get("APP_PASSWORD", "demo123")
AI_TIMEOUT_MS: int = int(os.environ.get("AI_TIMEOUT_MS", "90000"))
UI_TIMEOUT_MS: int = int(os.environ.get("UI_TIMEOUT_MS", "15000"))


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {
        **browser_context_args,
        "viewport": {"width": 1280, "height": 900},
    }


@pytest.fixture
def page_logged_in(page: Page) -> Page:
    _do_login(page)
    return page


def _do_login(page: Page) -> None:
    page.goto(FRONTEND_URL, wait_until="domcontentloaded")
    page.click("button:has-text('Sign In')", timeout=UI_TIMEOUT_MS)
    expect(page.locator("#page-login")).to_be_visible(timeout=UI_TIMEOUT_MS)
    page.fill("#loginEmail", USERNAME)
    page.fill("#loginPassword", PASSWORD)
    page.click("button:has-text('Sign In to Online Banking')", timeout=UI_TIMEOUT_MS)
    expect(page.locator("#page-dashboard")).to_be_visible(timeout=UI_TIMEOUT_MS)


def _show_dash_tab(page: Page, tab_id: str) -> None:
    page.evaluate(f"showDashTab('{tab_id}', null)")
    page.wait_for_timeout(350)


def _navigate_to_chat(page: Page) -> None:
    page.click("[onclick*=\"navigate('chat')\"]", timeout=UI_TIMEOUT_MS)
    expect(page.locator("#page-chat")).to_be_visible(timeout=UI_TIMEOUT_MS)
    # initChat() renders the welcome message and suggestion list.
    # navigate() only shows the page div; we trigger initChat() explicitly.
    page.evaluate("if (typeof initChat === 'function') initChat()")
    page.wait_for_timeout(500)


def _send_chat_message(page: Page, message: str) -> str:
    if not page.locator("#page-chat").is_visible():
        _navigate_to_chat(page)
    page.locator("#chatInput").fill(message)
    page.locator("#sendBtn").click(timeout=UI_TIMEOUT_MS)
    page.wait_for_function(
        """() => {
            const bubbles = document.querySelectorAll('.bubble-nova');
            if (!bubbles.length) return false;
            const last = bubbles[bubbles.length - 1];
            return last.querySelector('.dot') === null && last.innerText.trim().length > 5;
        }""",
        timeout=AI_TIMEOUT_MS,
    )
    bubbles = page.locator(".bubble-nova").all()
    return bubbles[-1].inner_text().strip()


# ── Test 1: Landing Page ──────────────────────────────────────────────────────

class TestLandingPage:
    def test_landing_page_loads(self, page: Page) -> None:
        page.goto(FRONTEND_URL, wait_until="domcontentloaded")
        expect(page.locator("#page-landing")).to_be_visible(timeout=UI_TIMEOUT_MS)
        assert "Pinnacle" in page.content()

    def test_sign_in_button_reveals_login_form(self, page: Page) -> None:
        page.goto(FRONTEND_URL, wait_until="domcontentloaded")
        page.click("button:has-text('Sign In')", timeout=UI_TIMEOUT_MS)
        expect(page.locator("#page-login")).to_be_visible(timeout=UI_TIMEOUT_MS)
        expect(page.locator("#loginEmail")).to_be_visible(timeout=UI_TIMEOUT_MS)
        expect(page.locator("#loginPassword")).to_be_visible(timeout=UI_TIMEOUT_MS)


# ── Test 2: Authentication ────────────────────────────────────────────────────

class TestAuthentication:
    def test_successful_login(self, page: Page) -> None:
        _do_login(page)
        expect(page.locator("#page-dashboard")).to_be_visible(timeout=UI_TIMEOUT_MS)

    def test_wrong_password_shows_error(self, page: Page) -> None:
        page.goto(FRONTEND_URL, wait_until="domcontentloaded")
        page.click("button:has-text('Sign In')", timeout=UI_TIMEOUT_MS)
        page.fill("#loginEmail", USERNAME)
        page.fill("#loginPassword", "wrong_password_xyz")
        page.click("button:has-text('Sign In to Online Banking')", timeout=UI_TIMEOUT_MS)
        expect(page.locator("#loginError")).to_be_visible(timeout=UI_TIMEOUT_MS)

    def test_logout_via_sidebar(self, page_logged_in: Page) -> None:
        page = page_logged_in
        page.click("#appSidebar button[onclick='logout()']", timeout=UI_TIMEOUT_MS)
        expect(page.locator("#page-landing")).to_be_visible(timeout=UI_TIMEOUT_MS)

    def test_logout_via_profile_dropdown(self, page_logged_in: Page) -> None:
        page = page_logged_in
        page.click("[onclick='toggleProfileMenu()']", timeout=UI_TIMEOUT_MS)
        expect(page.locator("#profileDropdown")).to_be_visible(timeout=UI_TIMEOUT_MS)
        page.locator("#profileDropdown button[onclick='logout()']").click(timeout=UI_TIMEOUT_MS)
        expect(page.locator("#page-landing")).to_be_visible(timeout=UI_TIMEOUT_MS)


# ── Test 3: Dashboard Overview ────────────────────────────────────────────────

class TestDashboardOverview:
    def test_greeting_contains_users_name(self, page_logged_in: Page) -> None:
        greeting = page_logged_in.locator("#greetingText").inner_text()
        assert "Alice" in greeting, f"Expected Alice in: {greeting!r}"

    def test_total_balance_shows_dollar_amount(self, page_logged_in: Page) -> None:
        text = page_logged_in.locator("#totalBalance").inner_text()
        assert "$" in text, f"No dollar sign in balance: {text!r}"

    def test_checking_balance_is_50k(self, page_logged_in: Page) -> None:
        text = page_logged_in.locator("#overviewChecking").inner_text()
        assert "50,000" in text, f"Unexpected checking balance: {text!r}"

    def test_savings_balance_shows_dollar(self, page_logged_in: Page) -> None:
        text = page_logged_in.locator("#overviewSavings").inner_text()
        assert "$" in text

    def test_investment_balance_shows_dollar(self, page_logged_in: Page) -> None:
        text = page_logged_in.locator("#overviewInvest").inner_text()
        assert "$" in text

    def test_at_least_five_recent_transactions(self, page_logged_in: Page) -> None:
        rows = page_logged_in.locator("#recentTxList .tx-row")
        assert rows.count() >= 5, f"Expected >=5 rows, got {rows.count()}"


# ── Test 4: Dashboard Tabs ────────────────────────────────────────────────────

class TestDashboardTabs:
    def test_accounts_tab(self, page_logged_in: Page) -> None:
        _show_dash_tab(page_logged_in, "accounts")
        expect(page_logged_in.locator("#section-accounts")).to_be_visible(timeout=UI_TIMEOUT_MS)
        expect(page_logged_in.locator("#acctChkBalance")).to_be_visible(timeout=UI_TIMEOUT_MS)

    def test_transactions_tab(self, page_logged_in: Page) -> None:
        _show_dash_tab(page_logged_in, "transactions")
        expect(page_logged_in.locator("#section-transactions")).to_be_visible(timeout=UI_TIMEOUT_MS)
        assert page_logged_in.locator("#fullTxList .tx-row").count() >= 1

    def test_cards_tab(self, page_logged_in: Page) -> None:
        _show_dash_tab(page_logged_in, "cards")
        expect(page_logged_in.locator("#section-cards")).to_be_visible(timeout=UI_TIMEOUT_MS)
        expect(page_logged_in.locator("#freezeCardBtn")).to_be_visible(timeout=UI_TIMEOUT_MS)

    def test_transfers_tab_shows_both_forms(self, page_logged_in: Page) -> None:
        _show_dash_tab(page_logged_in, "transfers")
        expect(page_logged_in.locator("#section-transfers")).to_be_visible(timeout=UI_TIMEOUT_MS)
        expect(page_logged_in.locator("#internalAmount")).to_be_visible(timeout=UI_TIMEOUT_MS)
        expect(page_logged_in.locator("#zelleAmount")).to_be_visible(timeout=UI_TIMEOUT_MS)

    def test_portfolio_tab(self, page_logged_in: Page) -> None:
        _show_dash_tab(page_logged_in, "portfolio")
        expect(page_logged_in.locator("#section-portfolio")).to_be_visible(timeout=UI_TIMEOUT_MS)
        expect(page_logged_in.locator("#portValue")).to_be_visible(timeout=UI_TIMEOUT_MS)

    def test_markets_tab(self, page_logged_in: Page) -> None:
        _show_dash_tab(page_logged_in, "market")
        expect(page_logged_in.locator("#section-market")).to_be_visible(timeout=UI_TIMEOUT_MS)

    def test_settings_tab_prefills_user_name(self, page_logged_in: Page) -> None:
        _show_dash_tab(page_logged_in, "settings")
        name_val = page_logged_in.locator("#settingsName").input_value()
        assert "Alice" in name_val, f"Expected Alice, got: {name_val!r}"


# ── Test 5: Card Management ───────────────────────────────────────────────────

class TestCardManagement:
    def test_freeze_card(self, page_logged_in: Page) -> None:
        page = page_logged_in
        _show_dash_tab(page, "cards")
        btn = page.locator("#freezeCardBtn")
        # ensure unfrozen state
        if "Unfreeze" in btn.inner_text():
            btn.click(); page.wait_for_timeout(600)
        btn.click(timeout=UI_TIMEOUT_MS)
        expect(page.locator("div.fixed:has-text('frozen')")).to_be_visible(timeout=UI_TIMEOUT_MS)
        assert "Unfreeze" in btn.inner_text()

    def test_unfreeze_card(self, page_logged_in: Page) -> None:
        page = page_logged_in
        _show_dash_tab(page, "cards")
        btn = page.locator("#freezeCardBtn")
        # ensure frozen state
        if "Unfreeze" not in btn.inner_text():
            btn.click(); page.wait_for_timeout(600)
        btn.click(timeout=UI_TIMEOUT_MS)
        expect(page.locator("div.fixed:has-text('unfrozen')")).to_be_visible(timeout=UI_TIMEOUT_MS)
        assert "Freeze" in btn.inner_text()

    def test_card_displays_user_name(self, page_logged_in: Page) -> None:
        _show_dash_tab(page_logged_in, "cards")
        card_name = page_logged_in.locator("#cardName").inner_text()
        assert "ALICE" in card_name.upper(), f"Card name: {card_name!r}"


# ── Test 6: Transfers ─────────────────────────────────────────────────────────

class TestTransfers:
    def test_internal_transfer_success(self, page_logged_in: Page) -> None:
        _show_dash_tab(page_logged_in, "transfers")
        page_logged_in.fill("#internalAmount", "50")
        page_logged_in.click("button[onclick='doInternalTransfer()']", timeout=UI_TIMEOUT_MS)
        expect(page_logged_in.locator("div.fixed:has-text('Transfer of')")).to_be_visible(timeout=UI_TIMEOUT_MS)

    def test_internal_transfer_empty_amount_warns(self, page_logged_in: Page) -> None:
        _show_dash_tab(page_logged_in, "transfers")
        page_logged_in.fill("#internalAmount", "")
        page_logged_in.click("button[onclick='doInternalTransfer()']", timeout=UI_TIMEOUT_MS)
        expect(page_logged_in.locator("div.fixed:has-text('valid')")).to_be_visible(timeout=UI_TIMEOUT_MS)

    def test_zelle_transfer_success(self, page_logged_in: Page) -> None:
        _show_dash_tab(page_logged_in, "transfers")
        page_logged_in.fill("#zelleRecipient", "friend@example.com")
        page_logged_in.fill("#zelleAmount", "25")
        page_logged_in.click("button[onclick='doZelleTransfer()']", timeout=UI_TIMEOUT_MS)
        expect(page_logged_in.locator("div.fixed:has-text('Zelle payment')")).to_be_visible(timeout=UI_TIMEOUT_MS)

    def test_zelle_no_recipient_warns(self, page_logged_in: Page) -> None:
        _show_dash_tab(page_logged_in, "transfers")
        page_logged_in.fill("#zelleAmount", "10")
        page_logged_in.fill("#zelleRecipient", "")
        page_logged_in.click("button[onclick='doZelleTransfer()']", timeout=UI_TIMEOUT_MS)
        expect(page_logged_in.locator("div.fixed:has-text('recipient')")).to_be_visible(timeout=UI_TIMEOUT_MS)


# ── Test 7: Chat UI ───────────────────────────────────────────────────────────

class TestChatUI:
    def test_chat_opens_from_sidebar(self, page_logged_in: Page) -> None:
        _navigate_to_chat(page_logged_in)
        expect(page_logged_in.locator("#page-chat")).to_be_visible(timeout=UI_TIMEOUT_MS)

    def test_welcome_message_from_nova(self, page_logged_in: Page) -> None:
        _navigate_to_chat(page_logged_in)
        welcome = page_logged_in.locator(".bubble-nova").first
        expect(welcome).to_be_visible(timeout=UI_TIMEOUT_MS)
        text = welcome.inner_text()
        assert "Alice" in text or "Nova" in text, f"Welcome: {text[:200]!r}"

    def test_suggestion_list_populated(self, page_logged_in: Page) -> None:
        _navigate_to_chat(page_logged_in)
        items = page_logged_in.locator("#suggestionList button")
        assert items.count() >= 3, f"Expected suggestion buttons, got {items.count()}"

    def test_new_conversation_resets_chat(self, page_logged_in: Page) -> None:
        _navigate_to_chat(page_logged_in)
        page_logged_in.locator("#chatInput").fill("Hello!")
        page_logged_in.locator("#sendBtn").click(timeout=UI_TIMEOUT_MS)
        page_logged_in.wait_for_timeout(1000)
        page_logged_in.click("button[onclick='clearChat()']", timeout=UI_TIMEOUT_MS)
        page_logged_in.wait_for_timeout(500)
        expect(page_logged_in.locator(".bubble-nova").first).to_be_visible(timeout=UI_TIMEOUT_MS)


# ── Test 8: AI Bot Responses ─────────────────────────────────────────────────

class TestAIBotResponses:
    def _fresh_chat(self, page: Page) -> None:
        if not page.locator("#page-chat").is_visible():
            _navigate_to_chat(page)
        else:
            page.click("button[onclick='clearChat()']", timeout=UI_TIMEOUT_MS)
            page.wait_for_timeout(300)

    def test_balance_query(self, page_logged_in: Page) -> None:
        self._fresh_chat(page_logged_in)
        reply = _send_chat_message(page_logged_in, "What's my checking account balance?")
        assert reply and re.search(r"\$[\d,]+", reply), f"No balance amount in: {reply[:200]!r}"

    def test_recent_transactions_query(self, page_logged_in: Page) -> None:
        self._fresh_chat(page_logged_in)
        reply = _send_chat_message(page_logged_in, "Show me my recent transactions")
        assert reply
        assert any(k in reply.lower() for k in ["transaction", "payment", "transfer", "debit", "credit", "amount"]), \
            f"Missing transaction keywords in: {reply[:300]!r}"

    def test_market_conditions_query(self, page_logged_in: Page) -> None:
        self._fresh_chat(page_logged_in)
        reply = _send_chat_message(page_logged_in, "What are current market conditions?")
        assert reply
        assert any(k in reply.lower() for k in ["market", "stock", "price", "index", "equity", "trend"]), \
            f"Missing market keywords in: {reply[:300]!r}"

    def test_fraud_risk_query(self, page_logged_in: Page) -> None:
        self._fresh_chat(page_logged_in)
        reply = _send_chat_message(page_logged_in, "Check my fraud risk score")
        assert reply
        assert any(k in reply.lower() for k in ["risk", "fraud", "score", "alert", "suspicious", "activity"]), \
            f"Missing risk keywords in: {reply[:300]!r}"

    def test_loan_status_query(self, page_logged_in: Page) -> None:
        self._fresh_chat(page_logged_in)
        reply = _send_chat_message(page_logged_in, "What's the status of my loan application?")
        assert reply
        assert any(k in reply.lower() for k in ["loan", "credit", "application", "status", "pending", "approved"]), \
            f"Missing loan keywords in: {reply[:300]!r}"

    def test_investment_portfolio_query(self, page_logged_in: Page) -> None:
        self._fresh_chat(page_logged_in)
        reply = _send_chat_message(page_logged_in, "Explain my investment portfolio performance")
        assert reply
        assert any(k in reply.lower() for k in ["investment", "portfolio", "return", "stock", "fund", "performance"]), \
            f"Missing portfolio keywords in: {reply[:300]!r}"

    def test_compliance_rules_query(self, page_logged_in: Page) -> None:
        self._fresh_chat(page_logged_in)
        reply = _send_chat_message(page_logged_in, "What compliance rules apply to large transfers?")
        assert reply
        assert any(k in reply.lower() for k in ["compliance", "regulation", "rule", "transfer", "limit", "reporting", "aml"]), \
            f"Missing compliance keywords in: {reply[:300]!r}"

    def test_btc_price_query(self, page_logged_in: Page) -> None:
        self._fresh_chat(page_logged_in)
        reply = _send_chat_message(page_logged_in, "What's the BTC price today?")
        assert reply
        assert any(k in reply.lower() for k in ["bitcoin", "btc", "price", "crypto", "$", "usd"]), \
            f"Missing crypto keywords in: {reply[:300]!r}"

    def test_fund_transfer_help(self, page_logged_in: Page) -> None:
        self._fresh_chat(page_logged_in)
        reply = _send_chat_message(page_logged_in, "Can you help me with a fund transfer?")
        assert reply
        assert any(k in reply.lower() for k in ["transfer", "send", "account", "amount", "help", "assist"]), \
            f"Missing transfer keywords in: {reply[:300]!r}"

    def test_risk_assessment_query(self, page_logged_in: Page) -> None:
        self._fresh_chat(page_logged_in)
        reply = _send_chat_message(page_logged_in, "Run a risk assessment on my account activity")
        assert reply
        assert any(k in reply.lower() for k in ["risk", "assessment", "activity", "score", "analysis", "account"]), \
            f"Missing risk assessment keywords in: {reply[:300]!r}"

    def test_generic_hello_gets_substantial_reply(self, page_logged_in: Page) -> None:
        self._fresh_chat(page_logged_in)
        reply = _send_chat_message(page_logged_in, "Hello, what can you help me with?")
        assert len(reply) >= 30, f"Reply too short ({len(reply)} chars): {reply!r}"


# ── Test 9: Notifications ─────────────────────────────────────────────────────

class TestNotifications:
    def test_notification_bell_toggles_panel(self, page_logged_in: Page) -> None:
        page = page_logged_in
        notif_btn = page.locator("#notifBtn")
        if notif_btn.is_visible():
            notif_btn.click(timeout=UI_TIMEOUT_MS)
            expect(page.locator("#notifPanel")).to_be_visible(timeout=UI_TIMEOUT_MS)
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
        else:
            pytest.skip("Notification bell not visible")


# ── Test 10: Backend API Smoke Tests ─────────────────────────────────────────

class TestAPISmoke:
    def test_frontend_http_200(self, page: Page) -> None:
        resp = page.request.get(FRONTEND_URL)
        assert resp.status == 200, f"Frontend HTTP {resp.status}"

    def test_orchestrator_health(self, page: Page) -> None:
        resp = page.request.get(f"{ORCHESTRATOR_URL}/api/health")
        assert resp.status == 200, f"Orchestrator health HTTP {resp.status}"

    def test_agents_list_not_empty(self, page: Page) -> None:
        resp = page.request.get(f"{ORCHESTRATOR_URL}/api/agents")
        assert resp.status == 200
        data = resp.json()
        # API returns {"agents": [...]} or a plain list
        agents = data.get("agents", data) if isinstance(data, dict) else data
        assert isinstance(agents, list) and len(agents) >= 1, \
            f"Expected agent list, got: {data}"

    def test_login_api_returns_tokens(self, page: Page) -> None:
        import json
        resp = page.request.post(
            f"{ORCHESTRATOR_URL}/api/auth/login",
            data=json.dumps({"email": USERNAME, "password": PASSWORD}),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 200, f"Login HTTP {resp.status}: {resp.text()[:200]}"
        body = resp.json()
        assert "access_token" in body and "refresh_token" in body
