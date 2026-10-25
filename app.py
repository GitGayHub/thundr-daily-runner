#!/usr/bin/env python3
"""Thundr Quick Switcher Web & Desktop Application Server.

Cross-platform app for Windows and Android:
- Provides a responsive web portal / PWA for quick account swapping.
- 1-click launch into Google Chrome with an isolated temporary profile and injected session.
- Syncs latest account balances, tokens, and streaks directly from GitHub Actions artifacts.
"""

import http.server
import json
import logging
import os
import socket
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any, Dict

# When started with pythonw.exe there is no console: send output to a log file
if sys.stdout is None or sys.stderr is None:
    _log_dir = Path(__file__).resolve().parent / "data"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _log_fh = open(_log_dir / "server.log", "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = _log_fh
    if sys.stderr is None:
        sys.stderr = _log_fh

# Fix Windows console cp1251 encoding issues with unicode characters
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from thundr.account import AccountManager
from thundr.launcher import (
    export_accounts_for_mobile,
    export_accounts_payload,
    launch_chrome_with_account,
    launch_clean_chrome,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("thundr.app")

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"


def get_local_ip() -> str:
    """Detect local LAN IP address for Android phone connections."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class ThundrAppHandler(http.server.SimpleHTTPRequestHandler):
    """Custom HTTP handler serving the PWA and REST API endpoints."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/accounts":
            self._handle_api_accounts()
        elif parsed.path in ("/", "/index.html"):
            index_path = WEB_DIR / "index.html"
            content = index_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        elif parsed.path == "/manifest.json":
            manifest_path = WEB_DIR / "manifest.json"
            content = manifest_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        elif parsed.path in ("/thundr-swapper.apk", "/download/apk", "/app.apk"):
            apk_path = BASE_DIR / "thundr-swapper.apk"
            if apk_path.exists():
                content = apk_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.android.package-archive")
                self.send_header("Content-Disposition", 'attachment; filename="thundr-swapper.apk"')
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_response(404)
                self.end_headers()
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        if parsed.path == "/api/open-next":
            self._handle_api_open_next()
        elif parsed.path == "/api/open":
            self._handle_api_open(body)
        elif parsed.path == "/api/sync":
            self._handle_api_sync()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_api_accounts(self):
        mgr = AccountManager()
        accounts = mgr.list_accounts(enabled_only=False)
        payload = export_accounts_payload(accounts)
        data = json.dumps(payload).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_api_open(self, body: bytes):
        try:
            req_data = json.loads(body.decode("utf-8")) if body else {}
            username = req_data.get("username", "").strip()
            if not username:
                raise ValueError("Missing 'username' field")

            mgr = AccountManager()
            acc = mgr.get_account(username)
            if not acc:
                raise ValueError(f"Account '{username}' not found")

            # Launch a clean Chrome (no cookies/profile) logged into this account
            ok = launch_clean_chrome(acc)
            resp_payload = {"ok": ok, "username": username}
            if not ok:
                resp_payload["error"] = "Failed to launch Chrome executable"
        except Exception as e:
            logger.error(f"Error handling /api/open: {e}")
            resp_payload = {"ok": False, "error": str(e)}

        data = json.dumps(resp_payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_api_open_next(self):
        """Open the next account in a clean Chrome (single-button flow)."""
        try:
            mgr = AccountManager()
            accounts = [a for a in mgr.list_accounts(enabled_only=False) if a.session and a.session.hashed_token]
            if not accounts:
                raise ValueError("No accounts with a session")
            state_file = BASE_DIR / "data" / "next_index.txt"
            try:
                idx = int(state_file.read_text().strip())
            except Exception:
                idx = 0
            acc = accounts[idx % len(accounts)]
            state_file.write_text(str((idx + 1) % len(accounts)))
            ok = launch_clean_chrome(acc)
            resp_payload = {"ok": ok, "username": acc.username, "number": idx % len(accounts) + 1, "total": len(accounts)}
            if not ok:
                resp_payload["error"] = "Failed to launch Chrome"
        except Exception as e:
            logger.error(f"Error handling /api/open-next: {e}")
            resp_payload = {"ok": False, "error": str(e)}

        data = json.dumps(resp_payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_api_sync(self):
        import subprocess
        import shutil
        import tempfile

        synced = False
        repo = os.getenv("GITHUB_REPOSITORY", "GitGayHub/thundr-daily-runner")
        target_file = BASE_DIR / "data" / "accounts.json"

        if shutil.which("gh"):
            try:
                with tempfile.TemporaryDirectory() as tmp_dir:
                    res = subprocess.run(
                        ["gh", "run", "download", "--repo", repo, "-n", "service-artifacts", "-D", tmp_dir],
                        capture_output=True,
                        text=True,
                        timeout=25,
                    )
                    if res.returncode == 0:
                        src_accounts = Path(tmp_dir) / "accounts.json"
                        if src_accounts.exists():
                            shutil.copy2(src_accounts, target_file)
                            synced = True
            except Exception as e:
                logger.debug(f"gh sync error: {e}")

        mgr = AccountManager()
        accounts = mgr.list_accounts(enabled_only=False)
        resp_payload = {
            "ok": True,
            "synced_from_github": synced,
            "count": len(accounts),
        }

        data = json.dumps(resp_payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def run_app(port: int = 8080, open_browser: bool = True):
    """Run the Thundr Switcher Web App server."""
    local_ip = get_local_ip()
    server_address = ("0.0.0.0", port)

    # Bind server with SO_REUSEADDR
    class ReusableTCPServer(http.server.ThreadingHTTPServer):
        allow_reuse_address = True

    httpd = ReusableTCPServer(server_address, ThundrAppHandler)

    msg = (
        "\n" + "=" * 60 + "\n"
        "[+] THUNDR QUICK SWITCHER IS RUNNING!\n"
        f"[*] Windows: http://localhost:{port}\n"
        f"[*] Android: http://{local_ip}:{port}\n"
        + "=" * 60 + "\n"
    )
    try:
        print(msg)
    except Exception:
        sys.stdout.buffer.write(msg.encode("utf-8", errors="replace"))

    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        httpd.server_close()


if __name__ == "__main__":
    port_arg = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8888
    run_app(port=port_arg, open_browser=True)
