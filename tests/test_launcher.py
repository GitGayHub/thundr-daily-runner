import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from thundr.account import AccountRecord, AccountSession, DailyCrateStatus
from thundr.launcher import (
    create_session_injector_extension,
    export_accounts_for_mobile,
    find_chrome_executable,
    generate_bookmarklet,
    launch_chrome_with_account,
)


def test_find_chrome_executable():
    """Verify Chrome executable detection does not crash."""
    exe = find_chrome_executable()
    # On Windows machine where Chrome/Edge is installed, exe should be found
    assert exe is None or isinstance(exe, str)


def test_create_session_injector_extension(tmp_path):
    """Verify creation of unpacked extension with valid manifest and injection JS."""
    acc = AccountRecord(
        username="gamasek13",
        session=AccountSession(user_id="uid_123", hashed_token="tok_abc", email="gamasek13@test.com"),
    )
    ext_dir = tmp_path / "extension"
    create_session_injector_extension(acc, ext_dir)

    manifest_file = ext_dir / "manifest.json"
    inject_file = ext_dir / "inject.js"

    assert manifest_file.exists()
    assert inject_file.exists()

    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert manifest_data["manifest_version"] == 3
    assert "https://thundr.com/*" in manifest_data["content_scripts"][0]["matches"]

    inject_js = inject_file.read_text(encoding="utf-8")
    assert "gamasek13" in inject_js
    assert "uid_123" in inject_js
    assert "tok_abc" in inject_js
    assert "localStorage.setItem" in inject_js


def test_generate_bookmarklet():
    """Verify bookmarklet URL structure."""
    acc = AccountRecord(
        username="gamasek1",
        session=AccountSession(user_id="u1", hashed_token="t1"),
    )
    bkm = generate_bookmarklet(acc)
    assert bkm.startswith("javascript:(function(){")
    assert "gamasek1" in bkm
    assert "u1" in bkm
    assert "t1" in bkm
    assert "https://thundr.com" in bkm


def test_export_accounts_for_mobile():
    """Verify accounts are exported with correct minutes and sorting for mobile app."""
    a1 = AccountRecord(
        username="gamasek1",
        last_status=DailyCrateStatus(coin_balance=150, streak=3, available=False),
    )
    a2 = AccountRecord(
        username="gamasek2",
        last_status=DailyCrateStatus(coin_balance=450, streak=7, available=True),
    )
    a3 = AccountRecord(
        username="gamasek3",
        last_status=DailyCrateStatus(coin_balance=400, streak=2, available=False),
    )
    a4 = AccountRecord(
        username="gamasek4",
        last_status=DailyCrateStatus(coin_balance=350, streak=14, available=False),
    )

    items = export_accounts_for_mobile([a1, a2, a3, a4])
    assert len(items) == 4

    # Verification of minute calculations as requested by user:
    # 150 coins -> 1 min (50 unspendable)
    # 350 coins -> 3 min
    # 400 coins -> 4 min
    # 450 coins -> 10 min
    mapped = {x["username"]: x for x in items}
    assert mapped["gamasek1"]["minutes"] == 1
    assert mapped["gamasek2"]["minutes"] == 10
    assert mapped["gamasek2"]["is_jackpot"] is True
    assert mapped["gamasek3"]["minutes"] == 4
    assert mapped["gamasek4"]["minutes"] == 3
    assert mapped["gamasek4"]["is_jackpot"] is True

    # Ordering: highest minutes at the top
    assert items[0]["username"] == "gamasek2"  # 10 mins
    assert items[1]["username"] == "gamasek3"  # 4 mins
    assert items[2]["username"] == "gamasek4"  # 3 mins
    assert items[3]["username"] == "gamasek1"  # 1 min


@patch("subprocess.Popen")
@patch("thundr.launcher.find_chrome_executable")
def test_launch_chrome_with_account(mock_find_chrome, mock_popen, tmp_path):
    """Verify launch_chrome_with_account prepares extension and launches Chrome."""
    mock_find_chrome.return_value = "C:\\Chrome\\chrome.exe"
    acc = AccountRecord(
        username="gamasek1",
        session=AccountSession(user_id="u1", hashed_token="t1"),
    )

    ok = launch_chrome_with_account(acc)
    assert ok is True
    assert mock_popen.called
    cmd_args = mock_popen.call_args[0][0]
    assert cmd_args[0] == "C:\\Chrome\\chrome.exe"
    assert any("--user-data-dir=" in arg for arg in cmd_args)
    assert any("--load-extension=" in arg for arg in cmd_args)
    assert "https://thundr.com" in cmd_args
