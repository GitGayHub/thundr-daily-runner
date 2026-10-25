"""Chrome profile launcher and session injector for Thundr account quick-swap.

Supports Windows (launching Chrome with an isolated temporary profile and
pre-injected localStorage session) and Android (generating 1-tap bookmarklets,
mobile web app payload, and in-app WebView session switching).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from thundr.account import AccountRecord
from thundr.telegram import calculate_boost_minutes

logger = logging.getLogger("thundr.launcher")

# Common Chrome binary locations on Windows
CHROME_CANDIDATE_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
    # Edge fallback as Chromium-compatible browser
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
]


def find_chrome_executable() -> Optional[str]:
    """Locate the Google Chrome or Microsoft Edge executable on the system."""
    for path_str in CHROME_CANDIDATE_PATHS:
        if path_str and Path(path_str).is_file():
            return path_str

    # Check PATH
    for binary in ("chrome.exe", "chrome", "google-chrome", "chromium", "msedge.exe"):
        found = shutil.which(binary)
        if found and Path(found).is_file():
            return found

    return None


def create_session_injector_extension(account: AccountRecord, target_dir: Path) -> Path:
    """Create an unpacked Chromium extension that injects the account session into localStorage.

    Uses background service worker + chrome.tabs to reliably inject AFTER page load,
    which works with React/Next.js apps that read localStorage after hydration.
    Also includes content_scripts as belt-and-suspenders.
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    session = account.session
    user_id = session.user_id if session else ""
    token = session.hashed_token if session else ""
    email = (session.email if session else None) or account.username
    username = account.username

    user_payload = {
        "id": user_id,
        "hashed_token": token,
        "email": email,
        "username": username,
    }

    payload_json = json.dumps(user_payload)

    # Manifest V3 with scripting + tabs permissions for reliable injection
    manifest = {
        "name": f"Thundr Session - {username}",
        "version": "1.0",
        "manifest_version": 3,
        "permissions": ["scripting", "tabs", "storage"],
        "host_permissions": [
            "https://thundr.com/*",
            "https://*.thundr.com/*",
        ],
        "background": {
            "service_worker": "background.js"
        },
        "content_scripts": [
            {
                "matches": [
                    "https://thundr.com/*",
                    "https://*.thundr.com/*",
                ],
                "js": ["inject.js"],
                "run_at": "document_start",
                "world": "MAIN",
                "all_frames": False,
            }
        ],
    }

    manifest_file = target_dir / "manifest.json"
    manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Content script — runs at document_start in MAIN world (before React reads localStorage)
    inject_js = f"""// Thundr session injector for: {username}
(function() {{
    const userPayload = {payload_json};
    const payloadStr = JSON.stringify(userPayload);
    try {{
        localStorage.setItem("user", payloadStr);
        sessionStorage.setItem("user", payloadStr);
        console.log("[ThundrSwitcher] Session pre-injected for {username}");
    }} catch (err) {{
        console.error("[ThundrSwitcher] inject failed:", err);
    }}
}})();
"""
    inject_file = target_dir / "inject.js"
    inject_file.write_text(inject_js, encoding="utf-8")

    # Background service worker — injects session then reloads page so React picks it up
    background_js = f"""// Background service worker for Thundr session injection
const PAYLOAD = {payload_json};
const PAYLOAD_STR = JSON.stringify(PAYLOAD);
const THUNDR_PATTERN = /^https:\\/\\/(www\\.)?thundr\\.com/;

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {{
    if (changeInfo.status === 'complete' && tab.url && THUNDR_PATTERN.test(tab.url)) {{
        chrome.scripting.executeScript({{
            target: {{ tabId: tabId }},
            world: 'MAIN',
            func: (payloadStr) => {{
                try {{
                    // Only inject+reload if not already logged in
                    var existing = localStorage.getItem('user');
                    var alreadyReloaded = sessionStorage.getItem('__thundr_injected__');
                    if (alreadyReloaded) return; // prevent reload loop
                    localStorage.setItem('user', payloadStr);
                    sessionStorage.setItem('user', payloadStr);
                    sessionStorage.setItem('__thundr_injected__', '1');
                    console.log('[ThundrBG] Session injected, reloading...');
                    location.reload();
                }} catch(e) {{}}
            }},
            args: [PAYLOAD_STR]
        }}).catch(() => {{}});
    }}
}});
"""
    bg_file = target_dir / "background.js"
    bg_file.write_text(background_js, encoding="utf-8")

    return target_dir


def launch_chrome_with_account(
    account: AccountRecord,
    temp_profile: bool = True,
    url: str = "https://thundr.com",
    auto_refresh_session: bool = True,
) -> bool:
    """Launch Chrome in a dedicated profile with the account pre-authenticated."""
    chrome_exe = find_chrome_executable()
    if not chrome_exe:
        logger.error("Chrome or Chromium-based browser executable not found on system.")
        return False

    # Ensure account has valid session tokens
    if (not account.session or not account.session.hashed_token) and auto_refresh_session:
        if account.has_credentials and account.password:
            try:
                from thundr.client import ThundrApiClient
                api = ThundrApiClient()
                session = api.login(account.username, account.password)
                account.session = session
                logger.info(f"Refreshed session for '{account.username}' via API before launch.")
            except Exception as e:
                logger.warning(f"Failed to auto-refresh session for '{account.username}': {e}")

    # Set up profile directory in workspace data/profiles
    workspace_dir = Path(__file__).resolve().parent.parent
    profiles_base = workspace_dir / "data" / "profiles"
    profile_dir = profiles_base / f"profile_{account.username}"
    profile_dir.mkdir(parents=True, exist_ok=True)

    # Clean up stale locks if any
    for lock_name in ("lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock_file = profile_dir / lock_name
        if lock_file.exists():
            try:
                lock_file.unlink()
            except Exception:
                pass

    # Set up unpacked extension (kept as fallback)
    ext_dir = profile_dir / "thundr_extension"
    create_session_injector_extension(account, ext_dir)

    # Find a free debug port
    import socket as _socket
    import time as _time

    def _find_free_port(start=9300, end=9400):
        for port in range(start, end):
            with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", port))
                    return port
                except OSError:
                    continue
        return None

    debug_port = _find_free_port()

    bat_file = profile_dir / "launch.bat"
    bat_content = f"""@echo off
start "" "{chrome_exe}" --user-data-dir="{profile_dir}" --load-extension="{ext_dir}" --disable-extensions-except="{ext_dir}" --no-first-run --no-default-browser-check --remote-debugging-port={debug_port} --new-window "{url}"
"""
    bat_file.write_text(bat_content, encoding="utf-8")

    try:
        logger.info(f"Launching Chrome for '{account.username}' in profile: {profile_dir} (debug port: {debug_port})")
        if os.name == "nt":
            task_name = f"ThundrOpen_{account.username}_{int(_time.time())}"
            bat_str = str(bat_file)
            subprocess.run(
                ["schtasks", "/create", "/tn", task_name, "/tr", bat_str,
                 "/sc", "once", "/st", "00:00", "/it", "/f"],
                capture_output=True, text=True
            )
            subprocess.run(["schtasks", "/run", "/tn", task_name], capture_output=True, text=True)
            _time.sleep(2)
            subprocess.run(["schtasks", "/delete", "/tn", task_name, "/f"], capture_output=True, text=True)

            # Now inject session via CDP
            if debug_port:
                _inject_session_via_cdp(account, debug_port, url)

            return True
        else:
            cmd = [
                chrome_exe,
                f"--user-data-dir={profile_dir}",
                f"--load-extension={ext_dir}",
                f"--disable-extensions-except={ext_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--new-window",
                url,
            ]
            subprocess.Popen(cmd)
            return True
    except Exception as exc:
        logger.error(f"Failed to launch Chrome for '{account.username}': {exc}")
        return False


def _inject_session_via_cdp(account: AccountRecord, debug_port: int, url: str, max_wait: int = 15) -> bool:
    """Connect to Chrome via CDP, inject session into localStorage, then reload."""
    import asyncio as _asyncio
    import time as _time

    session = account.session
    if not session:
        return False

    payload = {
        "id": session.user_id,
        "hashed_token": session.hashed_token,
        "email": session.email or account.username,
        "username": account.username,
    }
    payload_str = json.dumps(payload)

    async def _do_inject():
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                # Wait for Chrome to start listening
                for _ in range(max_wait):
                    try:
                        browser = await p.chromium.connect_over_cdp(f"http://localhost:{debug_port}", timeout=2000)
                        break
                    except Exception:
                        await _asyncio.sleep(1)
                else:
                    logger.warning(f"Could not connect to Chrome on port {debug_port}")
                    return False

                logger.info(f"Connected to Chrome on port {debug_port}")
                ctx = browser.contexts[0] if browser.contexts else None
                if not ctx:
                    return False

                # Wait for thundr page
                thundr_page = None
                for _ in range(10):
                    for pg in ctx.pages:
                        try:
                            if "thundr.com" in pg.url or "blank" not in pg.url.lower():
                                thundr_page = pg
                                break
                        except Exception:
                            pass
                    if thundr_page:
                        break
                    await _asyncio.sleep(1)

                if not thundr_page and ctx.pages:
                    thundr_page = ctx.pages[0]

                if not thundr_page:
                    logger.warning("No page found in Chrome")
                    return False

                # Wait for page load
                try:
                    await thundr_page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
                await _asyncio.sleep(1)

                # Inject session
                await thundr_page.evaluate(
                    "(s) => { localStorage.setItem('user', s); sessionStorage.setItem('user', s); }",
                    payload_str
                )
                logger.info(f"Session injected for '{account.username}' via CDP")

                # Reload so React picks up the session
                try:
                    await thundr_page.reload(wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass

                logger.info(f"Page reloaded for '{account.username}'")

                # Verify
                user_ls = await thundr_page.evaluate("() => localStorage.getItem('user')")
                if user_ls:
                    logger.info(f"Verified: localStorage 'user' set for '{account.username}'")
                else:
                    logger.warning(f"localStorage 'user' empty after inject for '{account.username}'")

                await browser.close()
                return True
        except Exception as e:
            logger.error(f"CDP inject error: {e}")
            return False

    try:
        loop = _asyncio.new_event_loop()
        result = loop.run_until_complete(_do_inject())
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Failed to run CDP inject: {e}")
        return False


def launch_clean_chrome(account: AccountRecord, url: str = "https://thundr.com") -> bool:
    """Open a fresh clean Chrome (no cookies/profile) already logged into the account."""
    return launch_chrome_with_account(account, temp_profile=True, url=url)


def generate_bookmarklet(account: AccountRecord) -> str:
    """Generate a 1-tap browser bookmarklet for quick-swapping accounts on Android or PC.

    Users on Android or Chrome can save this as a bookmark:
    When on https://thundr.com, tapping the bookmarklet immediately switches to this account.
    """
    session = account.session
    user_id = session.user_id if session else ""
    token = session.hashed_token if session else ""
    email = (session.email if session else None) or account.username
    username = account.username

    payload = {
        "id": user_id,
        "hashed_token": token,
        "email": email,
        "username": username,
    }
    payload_json = json.dumps(payload)

    # URL-encoded javascript snippet
    js_code = (
        f"javascript:(function(){{"
        f"var u={payload_json};"
        f"localStorage.setItem('user',JSON.stringify(u));"
        f"sessionStorage.setItem('user',JSON.stringify(u));"
        f"alert('✅ Свап на: {username}');"
        f"location.href='https://thundr.com';"
        f"}})();"
    )
    return js_code


def natural_sort_key(s: str) -> List[Any]:
    """Helper for natural alphanumeric sorting (e.g. gamasek1, gamasek2, ..., gamasek10)."""
    import re
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", str(s))]


def export_accounts_for_mobile(accounts: List[AccountRecord]) -> List[Dict[str, Any]]:
    """Format account records into a lightweight JSON payload suitable for mobile apps and PWAs."""
    result = []
    for acc in accounts:
        st = acc.last_status
        bal = getattr(st, "coin_balance", 0) if st else (acc.balance or 0)
        streak = getattr(st, "streak", 0) if st else 0
        avail = getattr(st, "available", False) if st else False
        next_reset = getattr(st, "next_reset_at", "") if st else ""

        # Boost minutes calculation:
        # < 100 -> 0 min
        # 100-449 -> bal // 100 min (150 -> 1 min, 350 -> 3 min, 400 -> 4 min)
        # >= 450 -> 450 coins = 10 min, 1000 = 30 min, 2100 = 60 min
        mins = calculate_boost_minutes(bal)

        item = {
            "username": acc.username,
            "password": acc.password or "",
            "balance": bal,
            "minutes": mins,
            "streak": streak,
            "is_jackpot": bool(streak and streak % 7 == 0),
            "available": avail,
            "next_reset_at": next_reset,
            "user_id": acc.session.user_id if acc.session else "",
            "hashed_token": acc.session.hashed_token if acc.session else "",
            "bookmarklet": generate_bookmarklet(acc),
        }
        result.append(item)

    # Sort descending by minutes, then balance, then natural username
    def sort_key(x: Dict[str, Any]):
        return (-x["minutes"], -x["balance"], natural_sort_key(x["username"]))

    return sorted(result, key=sort_key)


def export_accounts_payload(accounts: List[AccountRecord]) -> Dict[str, Any]:
    """Format account records into a complete JSON payload with metadata and timestamps."""
    from datetime import datetime, timezone
    import time

    export_list = export_accounts_for_mobile(accounts)
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    now_epoch = int(time.time())

    total_coins = sum(a.get("balance", 0) for a in export_list)
    total_mins = sum(a.get("minutes", 0) for a in export_list)

    return {
        "updated_at": now_iso,
        "updated_at_epoch": now_epoch,
        "updated_at_display": now_dt.strftime("%d.%m.%Y %H:%M UTC"),
        "total_accounts": len(export_list),
        "total_coins": total_coins,
        "total_minutes": total_mins,
        "accounts": export_list,
    }


def save_exported_accounts(
    accounts: List[AccountRecord],
    paths: Optional[List[Path]] = None,
) -> Dict[str, Any]:
    """Save exported mobile data to files for app synchronization."""
    payload = export_accounts_payload(accounts)
    json_str = json.dumps(payload, indent=2, ensure_ascii=False)
    list_json_str = json.dumps(payload["accounts"], indent=2, ensure_ascii=False)

    if paths is None:
        base_dir = Path(__file__).resolve().parent.parent
        paths = [
            base_dir / "data" / "accounts_export.json",
            base_dir / "android" / "src" / "main" / "assets" / "accounts.json",
        ]

    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        # For android assets/accounts.json, write both format or payload
        p.write_text(json_str, encoding="utf-8")

    return payload

