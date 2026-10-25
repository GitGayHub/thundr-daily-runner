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


def _find_chrome_hwnds():
    import ctypes
    user32 = ctypes.windll.user32
    found = []

    def _cb(hwnd, lparam):
        if user32.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if cls.value == "Chrome_WidgetWin_1":
                n = user32.GetWindowTextLengthW(hwnd)
                if n > 0:
                    buff = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, buff, n + 1)
                    if "google chrome" in buff.value.lower():
                        found.append(hwnd)
        return True

    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(proto(_cb), 0)
    return found


def bring_window_to_front() -> bool:
    """Force the Chrome window to the foreground (beats Windows foreground lock)."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        ok = False
        for hwnd in _find_chrome_hwnds():
            hwnd = ctypes.c_void_p(hwnd)
            fg = user32.GetForegroundWindow()
            cur_tid = kernel32.GetCurrentThreadId()
            fg_tid = user32.GetWindowThreadProcessId(ctypes.c_void_p(fg), None) if fg else 0
            attached = False
            if fg_tid and fg_tid != cur_tid:
                attached = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
            # A synthetic ALT press unlocks SetForegroundWindow
            user32.keybd_event(0x12, 0, 0, 0)
            user32.keybd_event(0x12, 0, 2, 0)
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
            HWND_TOPMOST, HWND_NOTOPMOST = ctypes.c_void_p(-1), ctypes.c_void_p(-2)
            flags = 0x0001 | 0x0002 | 0x0040  # NOSIZE | NOMOVE | SHOWWINDOW
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
            user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
            if attached:
                user32.AttachThreadInput(cur_tid, fg_tid, False)
            if user32.GetForegroundWindow() == hwnd.value:
                ok = True
        return ok
    except Exception as e:
        print(f"[clean_browser] Bring to front notice: {e}", flush=True)
        return False


def keep_in_front(seconds: float = 10.0) -> None:
    """Retry until Chrome really holds the foreground."""
    end = time.time() + seconds
    while time.time() < end:
        if bring_window_to_front():
            print("[clean_browser] Chrome is in foreground", flush=True)
            return
        time.sleep(0.4)
    print("[clean_browser] Could not confirm foreground", flush=True)


def build_init_js(payload: dict) -> str:
    """JS run before every page load: session + dark theme + full default settings.

    Thundr only lets "Start" go to the chat when BOTH `user` and `settings`
    exist in localStorage (otherwise it redirects to /signin), and the chat
    page reads `settings.filters.countries`, `user.created_at`, etc., so the
    objects must look like the ones the site itself creates on sign-in.
    """
    username = payload.get("username", "account")
    user = {"created_at": 0, "onboarded": True, **payload}
    user_js = json.dumps(json.dumps(user))
    return (
        "try {"
        f" var u = JSON.parse({user_js});"
        " if (!u.created_at) u.created_at = Date.now();"
        " var oldU = null; try { oldU = JSON.parse(localStorage.getItem('user') || 'null'); } catch (e) {}"
        " if (oldU && oldU.id === u.id && oldU.created_at) u.created_at = oldU.created_at;"
        " localStorage.setItem('user', JSON.stringify(u));"
        " sessionStorage.setItem('user', JSON.stringify(u));"
        " if (!sessionStorage.getItem('__prefs_applied')) {"
        "   localStorage.setItem('theme', 'dark');"
        "   localStorage.setItem('theme-bg', '#111');"
        "   sessionStorage.setItem('__prefs_applied', '1');"
        " }"
        " var d = {device: null, userDevice: 'windows', platform: 'web', volume: {master: 0, output: 100},"
        "   autoRoll: {video: true, text: false}, performance: {lowPower: false, facePopup: false},"
        "   interests: {wait: 0, tags: []}, filters: {countries: [], regions: [], sex: [], max_wait: 3},"
        "   autoMod: true, mobile: {swipeSensitivity: 0, swipeSkip: true, haptics: true},"
        "   privacy: {hidden: false}, event: {looking_for: 'a'}, reviewed: null, notification: null};"
        " var st = {}; try { st = JSON.parse(localStorage.getItem('settings') || '{}') || {}; } catch (e) {}"
        " for (var k in d) { if (st[k] === undefined || st[k] === null && d[k] !== null) st[k] = d[k]; }"
        " st.filters = Object.assign({}, d.filters, st.filters);"
        " st.performance = Object.assign({}, d.performance, st.performance, {facePopup: true});"
        " localStorage.setItem('settings', JSON.stringify(st));"
        f" console.log('[clean_browser] Injected session for {username}');"
        "} catch (e) {}"
    )


def _setup_logging() -> None:
    """Under pythonw there is no console; write output to data/clean_browser.log."""
    try:
        os.chdir(PROJECT_ROOT)
        log_dir = PROJECT_ROOT / "data"
        log_dir.mkdir(parents=True, exist_ok=True)
        if sys.stdout is None or sys.stderr is None or not sys.stdout.isatty():
            f = open(log_dir / "clean_browser.log", "a", encoding="utf-8", buffering=1)
            sys.stdout = f
            sys.stderr = f
    except Exception:
        pass


def main() -> None:
    _setup_logging()
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
    init_js = build_init_js(payload)

    print(f"[clean_browser] Starting Chrome for {username}...", flush=True)
    with sync_playwright() as p:
        launch_args = [
            "--start-maximized",
            "--no-default-browser-check",
            "--no-first-run",
            "--disable-blink-features=AutomationControlled",
            "--use-fake-ui-for-media-stream",
        ]
        try:
            browser = p.chromium.launch(channel="chrome", headless=False, args=launch_args,
                                      ignore_default_args=["--enable-automation"])
        except Exception:
            browser = p.chromium.launch(headless=False, args=launch_args,
                                      ignore_default_args=["--enable-automation"])

        ctx = browser.new_context(no_viewport=True)
        for origin in ("https://thundr.com", "https://www.thundr.com"):
            try:
                ctx.grant_permissions(["camera", "microphone"], origin=origin)
            except Exception as exc:
                print(f"[clean_browser] grant_permissions notice: {exc}", flush=True)
        ctx.add_init_script(init_js)
        page = ctx.new_page()
        page.goto(url)

        try:
            page.bring_to_front()
        except Exception:
            pass

        # Bring window to front on Windows desktop
        keep_in_front(8)

        # Save screenshot for proof and user verification
        try:
            page.wait_for_timeout(3000)
            proof_file = PROJECT_ROOT / "data" / "last_opened_account.png"
            proof_file.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(proof_file))
            print(f"[clean_browser] Screenshot saved: {proof_file}", flush=True)
        except Exception as exc:
            print(f"[clean_browser] Screenshot notice: {exc}", flush=True)

        keep_in_front(5)

        # Loop until browser is closed by user
        while browser.is_connected():
            time.sleep(1)


if __name__ == "__main__":
    main()
