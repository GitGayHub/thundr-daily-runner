"""Open a brand-new clean Chrome (no cookies, no profile) logged into one Thundr account.

Usage: python -m thundr.clean_browser '<json user payload>' [url]
Stays alive until the Chrome window is closed.
"""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright


def main() -> None:
    payload = json.loads(sys.argv[1])
    url = sys.argv[2] if len(sys.argv) > 2 else "https://thundr.com"
    payload_js = json.dumps(json.dumps(payload))
    init_js = (
        "if (location.hostname === 'thundr.com' || location.hostname.endsWith('.thundr.com')) {"
        f"try {{ localStorage.setItem('user', {payload_js}); sessionStorage.setItem('user', {payload_js}); }} catch (e) {{}}"
        "}"
    )

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=False, args=["--start-maximized"])
        except Exception:
            browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        ctx = browser.new_context(no_viewport=True)  # fresh context: no cookies
        ctx.add_init_script(init_js)
        page = ctx.new_page()
        page.goto(url)
        try:
            page.bring_to_front()
            state = page.evaluate("[document.visibilityState, document.hasFocus(), window.outerWidth, window.outerHeight]")
            print(f"[clean_browser] window state (visibility, focus, w, h): {state}", flush=True)
        except Exception as exc:
            print(f"[clean_browser] state check failed: {exc}", flush=True)
        while browser.is_connected():
            time.sleep(1)


if __name__ == "__main__":
    main()
