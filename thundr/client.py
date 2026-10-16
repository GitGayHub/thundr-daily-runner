"""
Hybrid HTTP API and Playwright browser client for Thundr.
Uses fast curl_cffi / requests for API calls and headless Playwright
for initial authentication and session renewal.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.parse
from typing import Any, Dict, Optional, Tuple

from thundr.account import (
    AccountRecord,
    AccountSession,
    DailyCrateStatus,
    mask_secret,
)

logger = logging.getLogger("thundr.client")

# Attempt curl_cffi import for TLS fingerprinting impersonation
try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False
    cffi_requests = None  # type: ignore

import requests as std_requests


class ThundrException(Exception):
    """Base exception for Thundr client errors."""
    pass


class ThundrAuthError(ThundrException):
    """Authentication or authorization failure (invalid token, bad login credentials)."""
    pass


class ThundrRateLimitError(ThundrException):
    """Rate limit exceeded (HTTP 429)."""
    pass


class ThundrApiError(ThundrException):
    """Generic Thundr API response error."""
    def __init__(self, message: str, status_code: Optional[int] = None, response_text: Optional[str] = None):
        full_msg = f"{message}: {response_text}" if response_text else message
        super().__init__(full_msg)
        self.status_code = status_code
        self.response_text = response_text


class ThundrNetworkError(ThundrException):
    """Network connection timeout or unreachable host."""
    pass


class ThundrApiClient:
    """Fast HTTP API client using curl_cffi with fallback to standard requests."""

    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        base_id_url: str = "https://id.thundr.com",
        base_web_url: str = "https://thundr.com",
        timeout: int = 25,
        max_retries: int = 3,
        retry_delay: float = 2.0,
        prefer_curl_cffi: bool = True,
    ):
        self.base_id_url = base_id_url.rstrip("/")
        self.base_web_url = base_web_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.prefer_curl_cffi = prefer_curl_cffi and HAS_CURL_CFFI

    def _request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, Dict[str, Any], str]:
        """Execute HTTP request with retries and failover."""
        req_headers = {
            "User-Agent": self.DEFAULT_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if headers:
            req_headers.update(headers)

        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                if self.prefer_curl_cffi and cffi_requests:
                    resp = cffi_requests.request(
                        method=method,
                        url=url,
                        params=params,
                        json=json_data,
                        headers=req_headers,
                        timeout=self.timeout,
                        impersonate="chrome120",
                    )
                    status_code = resp.status_code
                    text = resp.text
                else:
                    resp = std_requests.request(
                        method=method,
                        url=url,
                        params=params,
                        json=json_data,
                        headers=req_headers,
                        timeout=self.timeout,
                    )
                    status_code = resp.status_code
                    text = resp.text

                # Parse JSON if possible
                try:
                    data = json.loads(text) if text else {}
                except json.JSONDecodeError:
                    data = {}

                # Check status
                if status_code == 401:
                    raise ThundrAuthError(f"HTTP 401 Unauthorized for {url}: {text}")
                elif status_code == 429:
                    raise ThundrRateLimitError(f"HTTP 429 Rate limit on {url}")
                elif status_code == 409:
                    # 409 is often returned when crate is already claimed
                    return status_code, data, text
                elif 500 <= status_code < 600:
                    logger.warning(f"Server error {status_code} on {url} (attempt {attempt}/{self.max_retries})")
                    if attempt < self.max_retries:
                        time.sleep(self.retry_delay * attempt)
                        continue
                    raise ThundrApiError(f"HTTP {status_code} server error", status_code, text)

                return status_code, data, text

            except (ThundrAuthError, ThundrRateLimitError, ThundrApiError):
                raise
            except Exception as e:
                last_error = e
                logger.warning(f"Request failed ({type(e).__name__}: {e}) on {url} (attempt {attempt}/{self.max_retries})")
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay * attempt)
                else:
                    break

        raise ThundrNetworkError(f"Network error after {self.max_retries} attempts on {url}: {last_error}")

    def get_balance(self, user_id: str, hashed_token: str) -> int:
        """Fetch current coin balance for user."""
        encoded_token = urllib.parse.quote(hashed_token, safe="")
        url = f"{self.base_id_url}/api/coins/balance/{urllib.parse.quote(user_id, safe='')}?hashed_token={encoded_token}"
        status_code, data, text = self._request("GET", url)
        if status_code == 200:
            return int(data.get("coin_balance", 0))
        raise ThundrApiError(f"Failed to fetch balance: HTTP {status_code}", status_code, text)

    def get_crate_status(self, user_id: str, hashed_token: str) -> DailyCrateStatus:
        """Fetch daily crate availability and streak status."""
        encoded_token = urllib.parse.quote(hashed_token, safe="")
        url = f"{self.base_id_url}/api/coins/daily-crate/status/{urllib.parse.quote(user_id, safe='')}?hashed_token={encoded_token}"

        status_code, data, text = self._request("GET", url)
        if status_code == 200:
            return DailyCrateStatus.from_dict(data)
        raise ThundrApiError(f"Failed to fetch crate status: HTTP {status_code}", status_code, text)

    def claim_crate(self, user_id: str, hashed_token: str) -> Dict[str, Any]:
        """Claim the daily crate reward. Returns result dictionary."""
        url = f"{self.base_id_url}/api/coins/daily-crate/claim"
        status_code, data, text = self._request(
            "POST",
            url,
            json_data={"user_id": user_id, "hashed_token": hashed_token},
            headers={"Content-Type": "application/json"},
        )
        if status_code == 200:
            data["claimed"] = True
            return data
        elif status_code == 409:
            # Already claimed
            data["claimed"] = False
            data["already_claimed"] = True
            return data
        raise ThundrApiError(f"Failed to claim crate: HTTP {status_code}", status_code, text)

    def login(self, username: str, password: str) -> AccountSession:
        """Authenticate via direct HTTP API. Returns AccountSession with user_id and hashed_token."""
        url = f"{self.base_id_url}/api/users/login"
        status_code, data, text = self._request(
            "POST",
            url,
            json_data={"email": username, "password": password},
            headers={"Content-Type": "application/json"},
        )
        if status_code == 200:
            user_data = data.get("data") if isinstance(data.get("data"), dict) else data
            user_id = user_data.get("key") or user_data.get("user_id") or user_data.get("id")
            hashed_token = user_data.get("hashed_token") or user_data.get("token")
            if user_id and hashed_token:
                logger.info(f"Direct API login successful for user '{username}' (User ID: {user_id})")
                return AccountSession(
                    user_id=str(user_id),
                    hashed_token=str(hashed_token),
                    email=user_data.get("email") or username,
                )
            raise ThundrApiError(f"Unexpected response structure from login API: {text}", status_code, text)
        elif status_code == 401:
            raise ThundrAuthError(f"Invalid login credentials for '{username}': {text}")
        raise ThundrApiError(f"Login failed: HTTP {status_code}", status_code, text)


class ThundrBrowserClient:
    """Automates browser authentication using Playwright."""

    def __init__(self, headless: bool = True, timeout_sec: int = 40):
        self.headless = headless
        self.timeout_sec = timeout_sec

    async def async_login(self, username: str, password: str) -> AccountSession:
        """Perform headless login to Thundr and retrieve session token from localStorage."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ThundrException(
                "Playwright is not installed. Run `pip install playwright && playwright install chromium`."
            )

        logger.info(f"Initiating browser login for user '{username}' (headless={self.headless})...")

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 720},
            )
            page = await context.new_page()

            try:
                # Navigate to signup/signin form
                await page.goto("https://thundr.com/signup", wait_until="networkidle", timeout=self.timeout_sec * 1000)

                # Locate inputs
                user_input = page.locator("input[placeholder='Username']")
                pass_input = page.locator("input[placeholder='Password']")

                if await user_input.count() == 0 or await pass_input.count() == 0:
                    # Check /signin as fallback
                    await page.goto("https://thundr.com/signin", wait_until="networkidle", timeout=self.timeout_sec * 1000)
                    user_input = page.locator("input[placeholder='Username']")
                    pass_input = page.locator("input[placeholder='Password']")

                if await user_input.count() == 0 or await pass_input.count() == 0:
                    raise ThundrAuthError("Login inputs not found on Thundr website")

                await user_input.first.fill(username)
                await pass_input.first.fill(password)
                await page.wait_for_timeout(400)

                # Find and click 'Sign In' button
                signin_btn = page.locator("button:has-text('Sign In')")
                if await signin_btn.count() == 0:
                    signin_btn = page.locator("button[type='submit']")

                if await signin_btn.count() == 0:
                    raise ThundrAuthError("Sign In button not found on page")

                await signin_btn.first.click()

                # Poll for localStorage['user']
                start_time = time.time()
                user_data = None
                while time.time() - start_time < self.timeout_sec:
                    await page.wait_for_timeout(800)
                    raw_user = await page.evaluate("() => localStorage.getItem('user')")
                    if raw_user:
                        try:
                            user_data = json.loads(raw_user)
                            if user_data.get("id") and user_data.get("hashed_token"):
                                break
                        except json.JSONDecodeError:
                            pass

                    # Check for visible error banner
                    error_elements = await page.locator("div[role='alert'], .text-red-500, p:has-text('Invalid')").all()
                    for err_el in error_elements:
                        text = (await err_el.inner_text()).strip()
                        if text and any(kw in text.lower() for kw in ["invalid", "incorrect", "password", "error"]):
                            raise ThundrAuthError(f"Login failed: {text}")

                if not user_data or not user_data.get("hashed_token"):
                    raise ThundrAuthError("Authentication timed out: session token not received in localStorage")

                session = AccountSession(
                    user_id=user_data["id"],
                    hashed_token=user_data["hashed_token"],
                    email=user_data.get("email") or username,
                )
                logger.info(f"Browser login successful for '{username}' (User ID: {session.user_id}, Token: {session.token_masked})")
                return session

            finally:
                await browser.close()

    def login(self, username: str, password: str) -> AccountSession:
        """Synchronous wrapper for browser login."""
        return asyncio.run(self.async_login(username, password))


class ThundrClient:
    """Unified hybrid client: fast API first, automatic browser re-auth on token expiration."""

    def __init__(
        self,
        api_client: Optional[ThundrApiClient] = None,
        browser_client: Optional[ThundrBrowserClient] = None,
    ):
        self.api = api_client or ThundrApiClient()
        self.browser = browser_client or ThundrBrowserClient()

    def login(self, username: str, password: str) -> AccountSession:
        """Attempt fast direct API login with fallback to headless browser."""
        try:
            return self.api.login(username, password)
        except Exception as e:
            logger.warning(
                f"Direct API login failed for '{username}' ({type(e).__name__}: {e}), "
                f"falling back to Playwright browser..."
            )
            return self.browser.login(username, password)

    def ensure_valid_session(self, account: AccountRecord) -> AccountSession:
        """Check session validity, re-authenticating if missing or expired."""
        if account.has_session:
            # Test token with a lightweight balance or status check
            try:
                assert account.session is not None
                self.api.get_balance(account.session.user_id, account.session.hashed_token)
                return account.session
            except ThundrAuthError:
                logger.warning(f"Session token for '{account.username}' has expired or is invalid.")

        # Need to re-authenticate
        if not account.has_credentials:
            raise ThundrAuthError(
                f"Account '{account.username}' has no active session and no password to log in."
            )

        assert account.password is not None
        session = self.login(account.username, account.password)
        account.session = session
        return session

    def get_crate_status(self, account: AccountRecord, auto_reauth: bool = True) -> DailyCrateStatus:
        """Fetch crate status with automatic re-auth on 401."""
        if not account.has_session:
            if auto_reauth and account.has_credentials:
                self.ensure_valid_session(account)
            else:
                raise ThundrAuthError(f"No valid session for '{account.username}'")

        assert account.session is not None
        try:
            return self.api.get_crate_status(account.session.user_id, account.session.hashed_token)
        except ThundrAuthError:
            if auto_reauth and account.has_credentials:
                logger.info(f"Re-authenticating '{account.username}' via browser after 401...")
                session = self.browser.login(account.username, account.password)  # type: ignore
                account.session = session
                return self.api.get_crate_status(session.user_id, session.hashed_token)
            raise

    def claim_daily_crate(self, account: AccountRecord, auto_reauth: bool = True) -> Dict[str, Any]:
        """Claim daily crate with automatic re-auth on 401."""
        if not account.has_session:
            if auto_reauth and account.has_credentials:
                self.ensure_valid_session(account)
            else:
                raise ThundrAuthError(f"No valid session for '{account.username}'")

        assert account.session is not None
        try:
            return self.api.claim_crate(account.session.user_id, account.session.hashed_token)
        except ThundrAuthError:
            if auto_reauth and account.has_credentials:
                logger.info(f"Re-authenticating '{account.username}' via browser after 401...")
                session = self.browser.login(account.username, account.password)  # type: ignore
                account.session = session
                return self.api.claim_crate(session.user_id, session.hashed_token)
            raise

    def get_balance(self, account: AccountRecord, auto_reauth: bool = True) -> int:
        """Fetch coin balance with automatic re-auth on 401."""
        if not account.has_session:
            if auto_reauth and account.has_credentials:
                self.ensure_valid_session(account)
            else:
                raise ThundrAuthError(f"No valid session for '{account.username}'")

        assert account.session is not None
        try:
            return self.api.get_balance(account.session.user_id, account.session.hashed_token)
        except ThundrAuthError:
            if auto_reauth and account.has_credentials:
                session = self.browser.login(account.username, account.password)  # type: ignore
                account.session = session
                return self.api.get_balance(session.user_id, session.hashed_token)
            raise
