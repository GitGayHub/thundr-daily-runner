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
    """Create an unpacked temporary Chromium extension that injects the account session.

    The extension runs at 'document_start' on https://thundr.com and https://id.thundr.com,
    injecting the account's user session into localStorage before any page scripts execute.
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

    # Manifest V3 for modern Chrome
    manifest = {
        "name": f"Thundr Account Switcher - {username}",
        "version": "1.0",
        "manifest_version": 3,
        "content_scripts": [
            {
                "matches": [
                    "https://thundr.com/*",
                    "https://*.thundr.com/*",
                    "https://id.thundr.com/*",
                ],
                "js": ["inject.js"],
                "run_at": "document_start",
            }
        ],
    }

    manifest_file = target_dir / "manifest.json"
    manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # JavaScript that sets localStorage and sessionStorage
    inject_js = f"""// Auto-injected session for Thundr account: {username}
(function() {{
    const userPayload = {payload_json};
    try {{
        const payloadStr = JSON.stringify(userPayload);
        localStorage.setItem("user", payloadStr);
        sessionStorage.setItem("user", payloadStr);
        console.log("[ThundrSwitcher] Injected session for {username}");
    }} catch (err) {{
        console.error("[ThundrSwitcher] Failed to set localStorage:", err);
    }}
}})();
"""
    inject_file = target_dir / "inject.js"
    inject_file.write_text(inject_js, encoding="utf-8")

    return target_dir


def launch_chrome_with_account(
    account: AccountRecord,
    temp_profile: bool = True,
    url: str = "https://thundr.com",
    auto_refresh_session: bool = True,
) -> bool:
    """Launch Chrome in a temporary isolated profile with the account pre-authenticated.

    Args:
        account: The target AccountRecord.
        temp_profile: If True, uses a temporary profile directory under %TEMP%.
        url: The starting URL (default: https://thundr.com).
        auto_refresh_session: If True and account lacks a valid session token,
                              performs HTTP API login first to fetch one.

    Returns:
        True if Chrome was successfully launched, False otherwise.
    """
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

    # Set up profile directory
    base_temp = Path(tempfile.gettempdir())
    profile_dir = base_temp / f"thundr_profile_{account.username}"
    profile_dir.mkdir(parents=True, exist_ok=True)

    # Set up unpacked extension
    ext_dir = profile_dir / "thundr_extension"
    create_session_injector_extension(account, ext_dir)

    cmd = [
        chrome_exe,
        f"--user-data-dir={profile_dir}",
        f"--load-extension={ext_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-sync",
        "--new-window",
        url,
    ]

    try:
        logger.info(f"Launching Chrome for '{account.username}' in profile: {profile_dir}")
        subprocess.Popen(
            cmd,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        return True
    except Exception as exc:
        logger.error(f"Failed to launch Chrome for '{account.username}': {exc}")
        return False


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

