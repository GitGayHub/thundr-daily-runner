"""Open a brand-new clean Chrome (no cookies, no profile) logged into one Thundr account.

Usage:
    python -m thundr.clean_browser <username> [url]
    python -m thundr.clean_browser '<json user payload>' [url]
Stays alive until the Chrome window is closed.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import sync_playwright


def main() -> None:
    arg1 = sys.argv[1] if len(sys.argv) > 1 else ""
    url = sys.argv[2] if len(sys.argv) > 2 else "https://thundr.com"

    if not arg1:
        print("Usage: python -m thundr.clean_browser <username | json_payload> [url]", flush=True)
        return

    payload = None
    if arg1.strip().startswith("{"):
        try:
            payload = json.loads(arg1)
        except Exception as e:
            print(f"[clean_browser] JSON parse error: {e}", flush=True)

    if not payload:
        # Treat as username
        from thundr.account import AccountManager
        mgr = AccountManager()
        acc = mgr.get_account(arg1.strip())
        if not acc or not acc.session or not acc.session.hashed_token:
            # Auto-login if password available
            if acc and acc.password:
                try:
                    from thundr.client import ThundrApiClient
                    api = ThundrApiClient()
                    acc.session = api.login(acc.username, acc.password)
                except Exception as e:
                    print(f"[clean_browser] Auto-login failed: {e}", flush=True)

        if not acc or not acc.session:
            print(f"[clean_browser] Could not find account or session for '{arg1}'", flush=True)
            return

        payload = {
            "id": acc.session.user_id,
            "hashed_token": acc.session.hashed_token,
            "email": acc.session.email or acc.username,
            "username": acc.username,
        }

    username = payload.get("username", "account")
    payload_js = json.dumps(json.dumps(payload))
    init_js = (
        "try {"
        f" localStorage.setItem('user', {payload_js});"
        f" sessionStorage.setItem('user', {payload_js});"
        f" console.log('[clean_browser] Injected session for {username}');"
        "} catch (e) {}"
    )

    print(f"[clean_browser] Starting Chrome for {username}...", flush=True)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=False, args=["--start-maximized"])
        except Exception:
            browser = p.chromium.launch(headless=False, args=["--start-maximized"])

        ctx = browser.new_context(no_viewport=True)
        ctx.add_init_script(init_js)
        page = ctx.new_page()
        page.goto(url)

        try:
            page.bring_to_front()
            state = page.evaluate("[document.visibilityState, document.hasFocus(), window.outerWidth, window.outerHeight]")
            print(f"[clean_browser] window state: {state}", flush=True)
        except Exception as exc:
            print(f"[clean_browser] state check failed: {exc}", flush=True)

        # Loop until browser is closed
        while browser.is_connected():
            time.sleep(1)


if __name__ == "__main__":
    main()
