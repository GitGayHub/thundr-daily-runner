"""
Unit tests for thundr.telegram module.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from thundr.account import DailyCrateStatus
from thundr.state_machine import CrateState, StateMachineContext
from thundr.telegram import (
    TelegramNotifier,
    calculate_boost_minutes,
    format_streak_info,
)


def test_telegram_not_configured():
    notifier = TelegramNotifier(bot_token="", chat_id="")
    assert notifier.is_configured is False
    assert notifier.send_raw_message("hello") is False


def test_telegram_single_run_claimed_message():
    notifier = TelegramNotifier(
        bot_token="fake_bot_token",
        chat_id="fake_chat_id",
        mode="all",
        tz_name="Europe/Berlin",
    )

    ctx = StateMachineContext(
        username="john_doe",
        current_state=CrateState.CLAIMED,
        initial_balance=100,
        final_balance=125,
        coins_earned=25,
        status=DailyCrateStatus(
            available=False,
            coin_balance=125,
            streak=7,
            streak_bonus=True,
            next_reset_at="2026-10-17T00:00:00Z",
        ),
    )

    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        assert notifier.notify_single_run(ctx) is True

        payload = mock_post.call_args[1]["json"]
        assert payload["chat_id"] == "fake_chat_id"
        assert "john_doe" in payload["text"]
        assert "+25" in payload["text"]
        assert "125" in payload["text"]
        assert "Streak Bonus Activated" in payload["text"]
        # UTC 00:00 is 02:00 CEST (or 01:00 CET in winter)
        assert ("02:00:00" in payload["text"] or "01:00:00" in payload["text"])


def test_telegram_mode_filtering():
    ctx_claimed = StateMachineContext(username="u1", current_state=CrateState.CLAIMED)
    ctx_cooldown = StateMachineContext(username="u2", current_state=CrateState.COOLDOWN)
    ctx_failed = StateMachineContext(username="u3", current_state=CrateState.FAILED)

    # Mode: rewards_only
    n_rewards = TelegramNotifier(bot_token="t", chat_id="c", mode="rewards_only")
    assert n_rewards.should_notify_single(ctx_claimed) is True
    assert n_rewards.should_notify_single(ctx_cooldown) is False
    assert n_rewards.should_notify_single(ctx_failed) is False

    # Mode: errors_only
    n_errors = TelegramNotifier(bot_token="t", chat_id="c", mode="errors_only")
    assert n_errors.should_notify_single(ctx_claimed) is False
    assert n_errors.should_notify_single(ctx_cooldown) is False
    assert n_errors.should_notify_single(ctx_failed) is True

    # Mode: summary
    n_summary = TelegramNotifier(bot_token="t", chat_id="c", mode="summary")
    assert n_summary.should_notify_single(ctx_claimed) is False
    assert n_summary.should_notify_single(ctx_cooldown) is False

    # Mode: digest_2x (smart 2x daily notifications)
    n_digest = TelegramNotifier(bot_token="t", chat_id="c", mode="digest_2x")
    assert n_digest.should_notify_single(ctx_claimed) is False
    assert n_digest.should_notify_single(ctx_cooldown) is False
    assert n_digest.should_notify_single(ctx_failed) is False


def test_telegram_summary_report():
    notifier = TelegramNotifier(
        bot_token="fake_bot_token",
        chat_id="fake_chat_id",
        mode="summary",
    )

    c1 = StateMachineContext(
        username="user1", current_state=CrateState.CLAIMED, coins_earned=10, final_balance=50
    )
    c2 = StateMachineContext(
        username="user2", current_state=CrateState.COOLDOWN, initial_balance=20
    )

    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        assert notifier.notify_summary([c1, c2]) is True

        payload = mock_post.call_args[1]["json"]
        text = payload["text"]
        assert "THUNDR REPORT" in text
        assert "Награда за" in text
        assert "user1" in text  # user1 is top account with 50 coins
        assert "70 монет" in text  # total balance 50 + 20
        assert "35.0 монет" in text  # average balance 70 / 2
        assert "Сегодня выпало 50+:" in text
        assert "Среда:" in text


def test_telegram_environment_label(monkeypatch):
    # Local environment
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert TelegramNotifier.get_execution_environment_label() == "💻 Среда: Local PC"

    # GitHub Actions environment
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert TelegramNotifier.get_execution_environment_label() == "🌐 Среда: GitHub Actions"





def test_telegram_fallback_to_plain_text_on_parse_error():
    notifier = TelegramNotifier(bot_token="tok", chat_id="chat")

    with patch("requests.post") as mock_post:
        # First call fails with 400 Bad Request (bad HTML entity)
        resp_400 = MagicMock()
        resp_400.status_code = 400
        resp_400.text = "Bad Request: can't parse entities"

        # Second call succeeds
        resp_200 = MagicMock()
        resp_200.status_code = 200

        mock_post.side_effect = [resp_400, resp_200]

        ok = notifier.send_raw_message("<b>unclosed tag")
        assert ok is True
        assert mock_post.call_count == 2


def test_telegram_digest_2x_summary_throttle(tmp_path):
    notifier = TelegramNotifier(
        bot_token="fake_bot_token",
        chat_id="fake_chat_id",
        mode="digest_2x",
    )
    notifier.state_file = tmp_path / "telegram_state.json"

    c_cooldown = StateMachineContext(username="u1", current_state=CrateState.COOLDOWN)

    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        # First summary should send
        assert notifier.notify_summary([c_cooldown]) is True
        assert mock_post.call_count == 1

        # Second summary within 10h should be throttled
        assert notifier.notify_summary([c_cooldown]) is False
        assert mock_post.call_count == 1

        # If forced, it should send
        assert notifier.notify_summary([c_cooldown], force=True) is True
        assert mock_post.call_count == 2


def test_telegram_handle_commands():
    notifier = TelegramNotifier(bot_token="tok", chat_id="123")
    acc = MagicMock()
    acc.balance = 120
    acc.status = MagicMock()
    acc.status.available = True

    # Test /goal command returns executive summary
    goal_res = notifier.handle_command("/goal", accounts=[acc])
    assert goal_res is not None
    assert "THUNDR REPORT" in goal_res
    assert "СВОДКА" in goal_res
    assert "120" in goal_res

    # Test /summary command returns executive summary
    summary_res = notifier.handle_command("/summary", accounts=[acc])
    assert summary_res is not None
    assert "THUNDR REPORT — СВОДКА" in summary_res

    # Test /boost command
    boost_res = notifier.handle_command("/boost", accounts=[acc])
    assert boost_res is not None
    assert "BOOST STORE" in boost_res
    assert "Mini Boost" in boost_res

    # Test /status command
    status_res = notifier.handle_command("/status", accounts=[acc])
    assert status_res is not None
    assert "THUNDR СТАТУС" in status_res

    # Test /help command
    help_res = notifier.handle_command("/help")
    assert help_res is not None
    assert "/goal" in help_res
    assert "/boost" in help_res


def test_telegram_handle_commands_extended(tmp_path, monkeypatch):
    state_file = tmp_path / "tg_state.json"
    monkeypatch.setenv("TELEGRAM_STATE_FILE", str(state_file))
    notifier = TelegramNotifier(bot_token="tok", chat_id="123")

    acc = MagicMock()
    acc.username = "gamasek33"
    acc.balance = 120
    acc.status = MagicMock()
    acc.status.streak = 5
    acc.status.available = False

    # 1. /goal for specific account
    res_goal_user = notifier.handle_command("/goal gamasek33", accounts=[acc])
    assert res_goal_user is not None
    assert "gamasek33" in res_goal_user
    assert "Цель достигнута" in res_goal_user

    # 2. /goal for unknown account
    res_goal_unk = notifier.handle_command("/goal unknown_user", accounts=[acc])
    # Pool executive summary returned since account not found
    assert res_goal_unk is not None
    assert "THUNDR REPORT" in res_goal_unk
    assert "СВОДКА" in res_goal_unk

    # 3. /boost for specific account
    res_boost_user = notifier.handle_command("/boost gamasek33", accounts=[acc])
    assert res_boost_user is not None
    assert "gamasek33" in res_boost_user
    assert "120" in res_boost_user

    # 4. /boost buy with client mock
    mock_client = MagicMock()
    mock_client.spend_boost.return_value = {"message": "Success"}
    res_buy = notifier.handle_command("/boost buy gamasek33 mini", accounts=[acc], client=mock_client)
    assert res_buy is not None
    assert "успешно активирован" in res_buy
    assert mock_client.spend_boost.called

    # 5. State tracking: update_id and summary_ts persistence without conflict
    assert notifier.get_last_update_id() is None
    notifier.save_last_update_id(999)
    assert notifier.get_last_update_id() == 999

    notifier._save_last_summary_ts(123456.0)
    assert notifier._get_last_summary_ts() == 123456.0
    assert notifier.get_last_update_id() == 999


def test_telegram_summary_strict_claim_counting():
    """Verify that old claims or unreset accounts are NOT falsely counted as claimed today."""
    notifier = TelegramNotifier(bot_token="tok", chat_id="123")

    # Account with old claim date (e.g. 3 days ago)
    c_old = StateMachineContext(
        username="old_acc",
        current_state=CrateState.COOLDOWN,
        initial_balance=10,
        status=DailyCrateStatus(available=False, last_claimed_on="2026-10-15")
    )
    # Account with no status/reset info
    c_none = StateMachineContext(
        username="none_acc",
        current_state=CrateState.COOLDOWN,
        initial_balance=10,
        status=DailyCrateStatus(available=False, last_claimed_on=None, next_reset_at=None)
    )
    # Account claimed today
    c_today = StateMachineContext(
        username="today_acc",
        current_state=CrateState.CLAIMED,
        coins_earned=25,
        final_balance=35,
        status=DailyCrateStatus(available=False, last_claimed_on="2026-10-18")
    )

    report = notifier.format_summary_report([c_old, c_none, c_today])
    # Total accounts: 3. Only 1 claimed today!
    assert "Забрана частично (1/3)" in report
    assert "3/3" not in report


def test_telegram_help_and_start_environment_label(monkeypatch):
    """Verify that /help and /start include honest environment label."""
    notifier = TelegramNotifier(bot_token="tok", chat_id="123")

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    help_gh = notifier.handle_command("/help")
    assert "🌐 Среда: GitHub Actions" in help_gh

    start_gh = notifier.handle_command("/start")
    assert "🌐 Среда: GitHub Actions" in start_gh

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    help_local = notifier.handle_command("/help")
    assert "💻 Среда: Local PC" in help_local


def test_telegram_boost_buy_updates_balance():
    """Verify that /boost buy decrements coin_balance on the account object."""
    notifier = TelegramNotifier(bot_token="tok", chat_id="123")
    acc = MagicMock()
    acc.username = "gamasek33"
    acc.balance = 150
    acc.last_status = MagicMock()
    acc.last_status.coin_balance = 150

    mock_client = MagicMock()
    mock_client.spend_boost.return_value = {"message": "Success"}

    res = notifier.handle_command("/boost buy gamasek33 mini", accounts=[acc], client=mock_client)
    assert res is not None
    assert "успешно активирован" in res
    assert acc.last_status.coin_balance == 50  # 150 - 100


def test_telegram_determine_game_cycle_midnight_window():
    """Between 00:00 CEST and 02:00 CEST (before 00:00 UTC reset), active cycle is yesterday UTC."""
    notifier = TelegramNotifier(bot_token="tok", chat_id="123", tz_name="Europe/Berlin")

    ctx = StateMachineContext(
        username="u1",
        current_state=CrateState.COOLDOWN,
        status=DailyCrateStatus(
            available=False,
            last_claimed_on="2026-10-18",
            next_reset_at="2026-10-19T00:00:00Z",
            server_now="2026-10-18T22:23:00Z",
        ),
    )

    cycle_info = notifier.determine_game_cycle([ctx])
    assert cycle_info["cycle_date_str"] == "2026-10-18"
    assert cycle_info["cycle_display"] == "18.10"
    assert cycle_info["next_crate_display"] == "19.10"


def test_telegram_format_summary_midnight_window_no_false_claim():
    """At 00:23 CEST, report must state reward for 18.10 is claimed and next is in 1h 36m for 19.10."""
    notifier = TelegramNotifier(bot_token="tok", chat_id="123", tz_name="Europe/Berlin")

    contexts = [
        StateMachineContext(
            username=f"user_{i}",
            current_state=CrateState.COOLDOWN,
            initial_balance=50,
            final_balance=50,
            status=DailyCrateStatus(
                available=False,
                coin_balance=50,
                last_claimed_on="2026-10-18",
                next_reset_at="2026-10-19T00:00:00Z",
                server_now="2026-10-18T22:23:45Z",
            ),
        )
        for i in range(50)
    ]

    report = notifier.format_summary_report(contexts)

    # Must correctly identify reward for 18.10 as claimed on all 50 accounts
    assert "Награда за 18.10:</b> ✅ Забрана со всех (50/50)" in report

    # Must NEVER falsely claim that 19.10 was claimed
    assert "Награда за 19.10:</b> ✅ Забрана" not in report

    # Next crate line must indicate upcoming crate for 19.10
    assert "До следующего кейса (за 19.10):" in report


def test_telegram_format_summary_after_reset_not_claimed():
    """After 02:00 CEST (00:00 UTC), active cycle is 19.10, so accounts with 18.10 claims are not yet claimed."""
    notifier = TelegramNotifier(bot_token="tok", chat_id="123", tz_name="Europe/Berlin")

    contexts = [
        StateMachineContext(
            username=f"user_{i}",
            current_state=CrateState.READY,
            initial_balance=50,
            final_balance=50,
            status=DailyCrateStatus(
                available=True,
                coin_balance=50,
                last_claimed_on="2026-10-18",
                next_reset_at="2026-10-19T00:00:00Z",  # Reset has passed
                server_now="2026-10-19T00:05:00Z",
            ),
        )
        for i in range(50)
    ]

    report = notifier.format_summary_report(contexts)
    # Must report that 19.10 reward is available and NOT claimed
    assert "Награда за 19.10:</b> ⏳ Ещё не забрана (0/50 — доступна!)" in report
    assert "доступен сейчас! ⚡" in report


def test_telegram_format_summary_after_reset_claimed():
    """After reset and claiming on 19.10, report shows 19.10 claimed and next crate for 20.10."""
    notifier = TelegramNotifier(bot_token="tok", chat_id="123", tz_name="Europe/Berlin")

    contexts = [
        StateMachineContext(
            username=f"user_{i}",
            current_state=CrateState.CLAIMED,
            initial_balance=50,
            final_balance=60,
            coins_earned=10,
            status=DailyCrateStatus(
                available=False,
                coin_balance=60,
                last_claimed_on="2026-10-19",
                next_reset_at="2026-10-20T00:00:00Z",
                server_now="2026-10-19T00:10:00Z",
            ),
        )
        for i in range(50)
    ]

    report = notifier.format_summary_report(contexts)
    assert "Награда за 19.10:</b> ✅ Забрана со всех (50/50)" in report
    assert "До следующего кейса (за 20.10):" in report


def test_telegram_status_command_includes_reset_time():
    """Verify /status includes local reset time if reset timestamp is available."""
    notifier = TelegramNotifier(bot_token="tok", chat_id="123", tz_name="Europe/Berlin")
    acc = MagicMock()
    acc.username = "alice"
    acc.status = MagicMock()
    acc.status.available = False
    acc.status.next_reset_at = "2026-10-19T00:00:00Z"

    with patch.object(notifier, "format_time_remaining", return_value="1 ч 36 мин"):
        status_res = notifier.handle_command("/status", accounts=[acc])
        assert status_res is not None
        assert "1 ч 36 мин" in status_res
        assert "02:00:00" in status_res or "01:00:00" in status_res


def test_telegram_mismatch_header_shows_server_time():
    """During midnight window, header displays both local and server time to prevent confusion."""
    notifier = TelegramNotifier(bot_token="tok", chat_id="123", tz_name="Europe/Berlin")
    ctx = StateMachineContext(
        username="u1",
        current_state=CrateState.COOLDOWN,
        status=DailyCrateStatus(
            available=False,
            last_claimed_on="2026-10-18",
            next_reset_at="2026-10-19T00:00:00Z",
            server_now="2026-10-18T22:23:45Z",
        ),
    )
    report = notifier.format_summary_report([ctx])
    assert "сервер: 18.10 22:23 UTC" in report
    assert "Награда за 18.10:" in report


def test_telegram_determine_game_cycle_advances_stale_cache():
    """Verify that cached account with last_checked_at hours ago does not freeze time in the past."""
    from thundr.account import AccountRecord
    notifier = TelegramNotifier(bot_token="tok", chat_id="123", tz_name="Europe/Berlin")

    # Record checked 5 hours ago
    rec = AccountRecord(
        username="u1",
        last_checked_at="2026-10-18T17:00:00+00:00",
        last_status=DailyCrateStatus(
            available=False,
            last_claimed_on="2026-10-18",
            next_reset_at="2026-10-19T00:00:00Z",
            server_now="2026-10-18T17:00:00Z",
        ),
    )

    # If we evaluate now, server_now_dt must be advanced, not frozen at 17:00
    cycle_info = notifier.determine_game_cycle([rec])
    now_utc = datetime.now(timezone.utc)
    diff_from_now = abs((cycle_info["server_now_dt"] - now_utc).total_seconds())
    # Should be within seconds of live UTC, not 5+ hours in the past
    assert diff_from_now < 60


def test_telegram_dst_transition_europe_berlin():
    """Verify DST transition: 00:00 UTC reset displays as 02:00:00 in CEST and 01:00:00 in CET."""
    notifier = TelegramNotifier(bot_token="tok", chat_id="123", tz_name="Europe/Berlin")

    # Summer (CEST, UTC+2)
    summer_reset = "2026-07-15T00:00:00Z"
    assert "02:00:00" in notifier._to_local_str(summer_reset)

    # Winter (CET, UTC+1, after last Sunday of October)
    winter_reset = "2026-11-15T00:00:00Z"
    assert "01:00:00" in notifier._to_local_str(winter_reset)


def test_account_is_claimed_for_cycle():
    """Verify DailyCrateStatus.is_claimed_for_cycle works with ISO strings and next_reset_at fallback."""
    # 1. Exact date string
    s1 = DailyCrateStatus(available=False, last_claimed_on="2026-10-18")
    assert s1.is_claimed_for_cycle("2026-10-18") is True
    assert s1.is_claimed_for_cycle("2026-10-19") is False

    # 2. ISO timestamp format in last_claimed_on
    s2 = DailyCrateStatus(available=False, last_claimed_on="2026-10-18T14:30:00Z")
    assert s2.is_claimed_for_cycle("2026-10-18") is True

    # 3. None last_claimed_on, but in cooldown with next_reset_at
    s3 = DailyCrateStatus(available=False, last_claimed_on=None, next_reset_at="2026-10-19T00:00:00Z")
    assert s3.is_claimed_for_cycle("2026-10-18") is True
    assert s3.is_claimed_for_cycle("2026-10-19") is False


def test_stats_requested_flag_persistence(tmp_path, monkeypatch):
    state_file = tmp_path / "telegram_state.json"
    monkeypatch.setenv("TELEGRAM_STATE_FILE", str(state_file))

    notifier = TelegramNotifier(bot_token="tok", chat_id="123")
    assert notifier.is_stats_requested() is False

    notifier.set_stats_requested(True)
    assert notifier.is_stats_requested() is True
    assert state_file.exists()

    # Create new instance to ensure persistent file read
    notifier2 = TelegramNotifier(bot_token="tok", chat_id="123")
    assert notifier2.is_stats_requested() is True

    notifier2.clear_stats_requested()
    assert notifier2.is_stats_requested() is False

    notifier3 = TelegramNotifier(bot_token="tok", chat_id="123")
    assert notifier3.is_stats_requested() is False


def test_handle_command_stats(tmp_path, monkeypatch):
    state_file = tmp_path / "telegram_state.json"
    monkeypatch.setenv("TELEGRAM_STATE_FILE", str(state_file))

    notifier = TelegramNotifier(bot_token="tok", chat_id="123")
    assert notifier.is_stats_requested() is False

    reply = notifier.handle_command("/stats")
    assert reply is not None
    assert "Запрос на подробную статистику по всем аккаунтам принят" in reply
    assert "Будет отправлена при следующем запуске раннера" in reply
    assert notifier.is_stats_requested() is True

    # Test alias /stats@ThundrCoinsBot
    notifier.clear_stats_requested()
    assert notifier.is_stats_requested() is False
    reply2 = notifier.handle_command("/stats@ThundrCoinsBot")
    assert reply2 is not None
    assert notifier.is_stats_requested() is True


def test_format_stats_breakdown_natural_sorting():
    notifier = TelegramNotifier()
    # Unordered accounts with numbers
    accs = [
        {"username": "gamasek10", "balance": 100},
        {"username": "gamasek2", "balance": 25},
        {"username": "gamasek1", "balance": 15},
        {"username": "gamasek20", "balance": 200},
    ]
    messages = notifier.format_stats_breakdown(accs)
    assert len(messages) == 1
    msg = messages[0]
    assert "ДЕТАЛЬНАЯ СТАТИСТИКА ПО АККАУНТАМ" in msg
    assert "Всего аккаунтов: <b>4</b>" in msg
    assert "Общий баланс: <b>340</b> монет" in msg

    # Natural sorting check: gamasek1 -> gamasek2 -> gamasek10 -> gamasek20
    idx_1 = msg.find("gamasek1</code>: <b>15</b>")
    idx_2 = msg.find("gamasek2</code>: <b>25</b>")
    idx_10 = msg.find("gamasek10</code>: <b>100</b>")
    idx_20 = msg.find("gamasek20</code>: <b>200</b>")
    assert idx_1 < idx_2 < idx_10 < idx_20


def test_format_stats_breakdown_chunking():
    notifier = TelegramNotifier()
    # Create 60 accounts
    accs = [{"username": f"user{i}", "balance": i * 10} for i in range(1, 61)]
    # With low max_chars to force chunking
    messages = notifier.format_stats_breakdown(accs, max_chars=800)
    assert len(messages) > 1

    # Check that each message does not exceed max_chars
    for i, msg in enumerate(messages, 1):
        assert len(msg) <= 800
        assert f"({i}/{len(messages)})" in msg

    # Verify all accounts appear somewhere across the chunks
    combined = "\n".join(messages)
    for i in range(1, 61):
        assert f"user{i}" in combined


def test_format_stats_breakdown_empty():
    notifier = TelegramNotifier()
    messages = notifier.format_stats_breakdown([])
    assert len(messages) == 1
    assert "Нет данных об аккаунтах" in messages[0]


def test_send_stats_breakdown():
    notifier = TelegramNotifier(bot_token="tok", chat_id="123")
    accs = [{"username": f"acc_{i}", "balance": 10} for i in range(10)]

    with patch.object(notifier, "send_raw_message", return_value=True) as mock_send:
        ok = notifier.send_stats_breakdown(accs)
        assert ok is True
        assert mock_send.call_count >= 1


def test_format_stats_breakdown_with_account_dicts_and_last_status():
    notifier = TelegramNotifier()
    # Dicts as stored in accounts.json (containing last_status with coin_balance)
    accs = [
        {"username": "acc_a", "last_status": {"coin_balance": 120, "streak": 5}},
        {"username": "acc_b", "balance": 45},
        {"username": "acc_c", "coin_balance": 80},
    ]
    messages = notifier.format_stats_breakdown(accs)
    assert len(messages) == 1
    msg = messages[0]
    assert "acc_a</code>: <b>120</b> монет" in msg
    assert "acc_b</code>: <b>45</b> монет" in msg
    assert "acc_c</code>: <b>80</b> монет" in msg
    assert "Всего аккаунтов: <b>3</b>" in msg
    assert "Общий баланс: <b>245</b> монет" in msg


def test_format_stats_breakdown_state_machine_context_fallback(tmp_path, monkeypatch):
    from thundr.account import AccountManager
    from thundr.state_machine import StateMachineContext, CrateState
    acc_file = tmp_path / "accounts.json"
    monkeypatch.setenv("ACCOUNTS_FILE", str(acc_file))
    mgr = AccountManager()
    mgr.add_account("failing_acc", password="p")
    # Set known balance
    mgr.get_account("failing_acc").last_status = DailyCrateStatus(available=True, coin_balance=75, streak=3)
    mgr.save()

    notifier = TelegramNotifier()
    # Context with None initial and final balances (e.g. failed before check)
    failed_ctx = StateMachineContext(
        username="failing_acc",
        current_state=CrateState.FAILED,
        initial_balance=None,
        final_balance=None,
    )
    messages = notifier.format_stats_breakdown([failed_ctx])
    assert len(messages) == 1
    assert "failing_acc</code>: <b>75</b> монет" in messages[0]


def test_handle_command_russian_aliases_with_bot_mention(tmp_path, monkeypatch):
    state_file = tmp_path / "telegram_state.json"
    monkeypatch.setenv("TELEGRAM_STATE_FILE", str(state_file))

    notifier = TelegramNotifier(bot_token="tok", chat_id="123")

    # 1. /статс@ThundrCoinsBot
    notifier.clear_stats_requested()
    reply = notifier.handle_command("/статс@ThundrCoinsBot")
    assert reply is not None
    assert "Запрос на подробную статистику по всем аккаунтам принят" in reply
    assert notifier.is_stats_requested() is True

    # 2. /статистика@ThundrCoinsBot
    notifier.clear_stats_requested()
    reply = notifier.handle_command("/статистика@ThundrCoinsBot")
    assert reply is not None
    assert "Запрос на подробную статистику по всем аккаунтам принят" in reply
    assert notifier.is_stats_requested() is True


def test_calculate_boost_minutes():
    """Verify boost minute calculation across all tiers and edge cases."""
    # Zero or negative or invalid
    assert calculate_boost_minutes(0) == 0
    assert calculate_boost_minutes(-50) == 0
    assert calculate_boost_minutes(None) == 0
    assert calculate_boost_minutes("100") == 0

    # Less than 100 coins
    assert calculate_boost_minutes(25) == 0
    assert calculate_boost_minutes(99) == 0

    # Mini boost: 1 min = 100 coins
    assert calculate_boost_minutes(100) == 1
    assert calculate_boost_minutes(150) == 1
    assert calculate_boost_minutes(200) == 2
    assert calculate_boost_minutes(305) == 3
    assert calculate_boost_minutes(400) == 4

    # Small boost: 10 mins = 450 coins
    assert calculate_boost_minutes(450) == 10
    assert calculate_boost_minutes(500) == 10
    assert calculate_boost_minutes(550) == 11
    assert calculate_boost_minutes(900) == 20

    # Normal boost: 30 mins = 1000 coins
    assert calculate_boost_minutes(1000) == 30
    assert calculate_boost_minutes(1450) == 40
    assert calculate_boost_minutes(1900) == 50
    assert calculate_boost_minutes(2000) == 60
    assert calculate_boost_minutes(2100) == 61
    assert calculate_boost_minutes(2450) == 70
    assert calculate_boost_minutes(3000) == 90
    assert calculate_boost_minutes(5000) == 150
    assert calculate_boost_minutes(10000) == 300


def test_format_streak_info():
    """Verify streak formatting and slot machine replacement on 7-day jackpot."""
    assert format_streak_info(None) == "0 дн."
    assert format_streak_info(0) == "0 дн."
    assert format_streak_info(-3) == "0 дн."
    assert format_streak_info(1) == "🔥 1 дн."
    assert format_streak_info(5) == "🔥 5 дн."
    assert format_streak_info(6) == "🔥 6 дн."
    assert format_streak_info(7) == "🎰 7 дн."
    assert format_streak_info(8) == "🔥 8 дн."
    assert format_streak_info(13) == "🔥 13 дн."
    assert format_streak_info(14) == "🎰 14 дн."
    assert format_streak_info(21) == "🎰 21 дн."


def test_telegram_7_day_jackpot_in_single_run():
    """Verify notify_single_run swaps fire icon with slot machine on day 7 without tomorrow jackpot text."""
    notifier = TelegramNotifier(bot_token="tok", chat_id="123", mode="all")

    # Day 7 Claimed
    ctx_7 = StateMachineContext(
        username="lucky7",
        current_state=CrateState.CLAIMED,
        coins_earned=50,
        final_balance=200,
        status=DailyCrateStatus(streak=7, streak_bonus=True),
    )
    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        notifier.notify_single_run(ctx_7)
        text = mock_post.call_args[1]["json"]["text"]
        assert "🎰 <b>Серия (Streak):</b> 7 дн." in text

    # Day 6 Cooldown
    ctx_6 = StateMachineContext(
        username="almost6",
        current_state=CrateState.COOLDOWN,
        initial_balance=150,
        status=DailyCrateStatus(streak=6),
    )
    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        notifier.notify_single_run(ctx_6)
        text = mock_post.call_args[1]["json"]["text"]
        assert "🔥 <b>Серия (Streak):</b> 6 дн." in text
        assert "завтра джекпот" not in text


def test_telegram_7_day_jackpot_in_summary_report():
    """Verify format_summary_report highlights accounts on day 7 and omits tomorrow jackpot."""
    notifier = TelegramNotifier(bot_token="tok", chat_id="123")

    c1 = StateMachineContext(
        username="winner1",
        current_state=CrateState.CLAIMED,
        coins_earned=50,
        final_balance=100,
        status=DailyCrateStatus(streak=7),
    )
    c2 = StateMachineContext(
        username="winner2",
        current_state=CrateState.CLAIMED,
        coins_earned=50,
        final_balance=120,
        status=DailyCrateStatus(streak=14),
    )
    c3 = StateMachineContext(
        username="regular",
        current_state=CrateState.COOLDOWN,
        initial_balance=50,
        status=DailyCrateStatus(streak=3),
    )

    report = notifier.format_summary_report([c1, c2, c3])
    assert "💰 <b>Общий баланс:</b> 270 монет (2 мин) (+100)" in report
    assert "🎰 <b>Сегодня джекпот:</b> 2 акк." in report
    assert "🎁 <b>Сегодня выпало 50+:</b> 2 акк." in report
    assert "В среднем на 1 аккаунт:</b> 90.0 монет (0.7 мин)" in report
    assert "🏆 <b>Топ-аккаунт:</b> <code>winner2</code> (120 монет, 1 мин)" in report

    # Day 6 case should NOT add tomorrow jackpot line
    c_tmrw = StateMachineContext(
        username="tmrw_guy",
        current_state=CrateState.COOLDOWN,
        initial_balance=50,
        status=DailyCrateStatus(streak=6),
    )
    report_tmrw = notifier.format_summary_report([c_tmrw, c3])
    assert "Завтра день джекпота" not in report_tmrw


def test_telegram_7_day_jackpot_in_status_and_goal():
    """Verify handle_command highlights jackpot in /status and /goal and omits tomorrow jackpot."""
    notifier = TelegramNotifier(bot_token="tok", chat_id="123")

    acc7 = MagicMock()
    acc7.username = "jackpot_acc"
    acc7.balance = 100
    acc7.status = DailyCrateStatus(available=False, streak=7)

    # /status
    res_status = notifier.handle_command("/status", accounts=[acc7])
    assert "🎰 <b>Сегодня день джекпота (7-й день):</b>" in res_status
    assert "jackpot_acc" in res_status
    assert "Завтра день джекпота" not in res_status

    # /goal
    res_goal = notifier.handle_command("/goal jackpot_acc", accounts=[acc7])
    assert "🎰 Стрик: <b>7 дн.</b>" in res_goal

    # Day 6 in /status
    acc6 = MagicMock()
    acc6.username = "day6_acc"
    acc6.balance = 50
    acc6.status = DailyCrateStatus(available=False, streak=6)

    res_status6 = notifier.handle_command("/status", accounts=[acc6])
    assert "Завтра день джекпота" not in res_status6


def test_format_stats_minutes_breakdown():
    """Verify format_stats_minutes_breakdown computes boost minutes and includes pool totals."""
    notifier = TelegramNotifier()
    accs = [
        {"username": "gamasek1", "balance": 25, "streak": 2},
        {"username": "gamasek33", "balance": 100, "streak": 6},
        {"username": "gamasek13", "balance": 305, "streak": 7},
        {"username": "gamasek50", "balance": 1000, "streak": 14},
    ]

    messages = notifier.format_stats_minutes_breakdown(accs)
    assert len(messages) == 1
    msg = messages[0]

    assert "ДЕТАЛЬНАЯ СТАТИСТИКА ПО АККАУНТАМ (МИНУТЫ БУСТА)" in msg
    assert "Всего аккаунтов: <b>4</b>" in msg
    # Total coins: 25 + 100 + 305 + 1000 = 1430
    # Minutes: 0 + 1 + 3 + 30 = 34
    assert "⏱️ Всего минут: <b>34</b> мин" in msg
    assert "💰 1430 монет" in msg
    assert "в ср. 8.5 мин" in msg

    # Check account lines matching prompt specification:
    assert "• <code>gamasek1</code>: <b>0</b> мин (25 монет) 🔥 2 дн." in msg
    assert "• <code>gamasek33</code>: <b>1</b> мин (100 монет) 🔥 6 дн." in msg
    assert "• <code>gamasek13</code>: <b>3</b> мин (305 монет) 🎰 7 дн." in msg
    assert "• <code>gamasek50</code>: <b>30</b> мин (1000 монет) 🎰 14 дн." in msg
    assert "завтра джекпот" not in msg

    # Verify descending ordering: highest minutes at the top
    pos_50 = msg.index("<code>gamasek50</code>")
    pos_13 = msg.index("<code>gamasek13</code>")
    pos_33 = msg.index("<code>gamasek33</code>")
    pos_1 = msg.index("<code>gamasek1</code>")
    assert pos_50 < pos_13 < pos_33 < pos_1


def test_format_stats_minutes_breakdown_550_coins_optimal_sort():
    """Verify 550 coins calculates to 11 mins (450->10m + 100->1m) and sorts descending."""
    notifier = TelegramNotifier()
    accs = [
        {"username": "acc_zero", "balance": 50},
        {"username": "acc_550", "balance": 550},
        {"username": "acc_100", "balance": 100},
        {"username": "acc_1000", "balance": 1000},
    ]

    messages = notifier.format_stats_minutes_breakdown(accs)
    msg = messages[0]
    assert "• <code>acc_1000</code>: <b>30</b> мин (1000 монет)" in msg
    assert "• <code>acc_550</code>: <b>11</b> мин (550 монет)" in msg
    assert "• <code>acc_100</code>: <b>1</b> мин (100 монет)" in msg
    assert "• <code>acc_zero</code>: <b>0</b> мин (50 монет)" in msg

    # Order must be strictly descending by minutes: acc_1000 (30m) -> acc_550 (11m) -> acc_100 (1m) -> acc_zero (0m)
    assert (
        msg.index("<code>acc_1000</code>")
        < msg.index("<code>acc_550</code>")
        < msg.index("<code>acc_100</code>")
        < msg.index("<code>acc_zero</code>")
    )


def test_format_stats_coins_breakdown():
    """Verify format_stats_coins_breakdown outputs exact coins and streak callout."""
    notifier = TelegramNotifier()
    accs = [
        {"username": "gamasek1", "balance": 25, "streak": 2},
        {"username": "gamasek2", "balance": 30, "streak": 7},
    ]

    messages = notifier.format_stats_coins_breakdown(accs)
    assert len(messages) == 1
    msg = messages[0]

    assert "ДЕТАЛЬНАЯ СТАТИСТИКА ПО АККАУНТАМ" in msg
    assert "Всего аккаунтов: <b>2</b>" in msg
    assert "Общий баланс: <b>55</b> монет" in msg
    assert "• <code>gamasek1</code>: <b>25</b> монет (🔥 2 дн.)" in msg
    assert "• <code>gamasek2</code>: <b>30</b> монет (🎰 7 дн.)" in msg
    assert "завтра джекпот" not in msg


def test_stats_coins_requested_flag_persistence(tmp_path, monkeypatch):
    """Verify stats_coins_requested flag persists across TelegramNotifier instances."""
    state_file = tmp_path / "telegram_state.json"
    monkeypatch.setenv("TELEGRAM_STATE_FILE", str(state_file))

    notifier1 = TelegramNotifier(bot_token="tok", chat_id="123")
    assert notifier1.is_stats_coins_requested() is False

    notifier1.set_stats_coins_requested(True)
    assert notifier1.is_stats_coins_requested() is True
    assert state_file.exists()

    notifier2 = TelegramNotifier(bot_token="tok", chat_id="123")
    assert notifier2.is_stats_coins_requested() is True

    notifier2.clear_stats_coins_requested()
    assert notifier2.is_stats_coins_requested() is False

    notifier3 = TelegramNotifier(bot_token="tok", chat_id="123")
    assert notifier3.is_stats_coins_requested() is False


def test_handle_command_stats_coins_and_aliases(tmp_path, monkeypatch):
    """Verify /stats_coins and aliases trigger stats_coins_requested flag."""
    state_file = tmp_path / "telegram_state.json"
    monkeypatch.setenv("TELEGRAM_STATE_FILE", str(state_file))
    notifier = TelegramNotifier(bot_token="tok", chat_id="123")

    aliases = [
        "/stats_coins",
        "/stats_coins@ThundrCoinsBot",
        "/stats-coins",
        "/статс_монеты",
        "/статс-монеты",
        "/coins",
        "/coins@ThundrCoinsBot",
        "/монеты",
    ]

    for alias in aliases:
        notifier.clear_stats_coins_requested()
        assert notifier.is_stats_coins_requested() is False
        reply = notifier.handle_command(alias)
        assert reply is not None
        assert "Запрос на подробную статистику по монетам всех аккаунтов принят" in reply
        assert notifier.is_stats_coins_requested() is True


def test_send_stats_minutes_and_coins_breakdown():
    """Verify send_stats_minutes_breakdown and send_stats_coins_breakdown invoke Telegram API."""
    notifier = TelegramNotifier(bot_token="tok", chat_id="123")
    accs = [{"username": f"user_{i}", "balance": i * 50} for i in range(1, 5)]

    with patch.object(notifier, "send_raw_message", return_value=True) as mock_send:
        ok_min = notifier.send_stats_minutes_breakdown(accs)
        assert ok_min is True
        assert mock_send.call_count == 1

    with patch.object(notifier, "send_raw_message", return_value=True) as mock_send:
        ok_coin = notifier.send_stats_coins_breakdown(accs)
        assert ok_coin is True
        assert mock_send.call_count == 1


def test_format_streak_info_edge_cases():
    """Verify format_streak_info handles types, strings, negatives, and modulo 7 cycles."""
    assert format_streak_info(None) == "0 дн."
    assert format_streak_info("invalid") == "0 дн."
    assert format_streak_info(-5) == "0 дн."
    assert format_streak_info(0) == "0 дн."
    assert format_streak_info(1) == "🔥 1 дн."
    assert format_streak_info(6) == "🔥 6 дн."
    assert format_streak_info("6") == "🔥 6 дн."
    assert format_streak_info(7) == "🎰 7 дн."
    assert format_streak_info("7") == "🎰 7 дн."
    assert format_streak_info(13) == "🔥 13 дн."
    assert format_streak_info(14) == "🎰 14 дн."
    assert format_streak_info(21) == "🎰 21 дн."


def test_format_summary_report_dual_jackpot_both_displayed():
    """Verify format_summary_report shows day 7 jackpot and omits tomorrow jackpot."""
    notifier = TelegramNotifier()
    c7 = StateMachineContext(
        username="jackpot_user",
        current_state=CrateState.COOLDOWN,
        initial_balance=100,
        status=DailyCrateStatus(streak=7),
    )
    c6 = StateMachineContext(
        username="near_user",
        current_state=CrateState.COOLDOWN,
        initial_balance=50,
        status=DailyCrateStatus(streak=6),
    )
    c2 = StateMachineContext(
        username="regular_user",
        current_state=CrateState.COOLDOWN,
        initial_balance=20,
        status=DailyCrateStatus(streak=2),
    )

    report = notifier.format_summary_report([c7, c6, c2])
    assert "🎰 <b>Сегодня джекпот:</b> 1 акк." in report
    assert "Завтра день джекпота" not in report


def test_status_command_with_dict_accounts_and_dual_jackpots():
    """Verify /status correctly parses streaks from dicts and omits tomorrow jackpot."""
    notifier = TelegramNotifier()
    accs = [
        {"username": "acc_jp", "balance": 100, "status": {"streak": 7, "available": True}},
        {"username": "acc_near", "balance": 50, "status": {"streak": 6, "available": False}},
        {"username": "acc_reg", "balance": 20, "status": {"streak": 3, "available": False}},
    ]
    reply = notifier.handle_command("/status", accounts=accs)
    assert reply is not None
    assert "Сегодня день джекпота (7-й день)" in reply
    assert "acc_jp" in reply
    assert "Завтра день джекпота" not in reply


def test_boost_store_durations_in_telegram():
    """Verify /boost displays correct minute and hour durations matching store screenshot."""
    notifier = TelegramNotifier()
    reply = notifier.handle_command("/boost")
    assert reply is not None
    assert "Mini Boost:</b> 100 монет (1 мин)" in reply
    assert "Small Boost:</b> 450 монет (10 мин)" in reply
    assert "Normal Boost:</b> 1000 монет (30 мин)" in reply
    assert "Super Boost:</b> 2100 монет (1 час / 60 мин)" in reply


def test_handle_command_extended_singular_aliases():
    """Verify singular aliases /stat, /coin, /монета are handled properly."""
    notifier = TelegramNotifier()
    with patch.object(notifier, "set_stats_requested") as mock_stats, \
         patch.object(notifier, "set_stats_coins_requested") as mock_coins:

        reply1 = notifier.handle_command("/stat")
        assert "Запрос на подробную статистику по всем аккаунтам принят" in reply1
        assert mock_stats.called

        reply2 = notifier.handle_command("/coin")
        assert "Запрос на подробную статистику по монетам" in reply2
        assert mock_coins.called

        reply3 = notifier.handle_command("/монета")
        assert "Запрос на подробную статистику по монетам" in reply3


def test_summary_report_150_plus_callout_and_short_50_plus():
    """Verify 50+ is concise (only count), 150+ lists accounts, jackpot is concise, and 0 claimed is not 'всё чётко'."""
    notifier = TelegramNotifier()

    # Case 1: uncollected accounts (0 claimed today)
    ctx_unclaimed = [
        StateMachineContext(
            username=f"acc_{i}",
            current_state=CrateState.READY,
            initial_balance=100,
            status=DailyCrateStatus(available=True, coin_balance=100, last_claimed_on="2026-10-22", next_reset_at="2026-10-24T00:00:00Z"),
        )
        for i in range(10)
    ]
    ref_dt = datetime(2026, 10, 23, 14, 0, tzinfo=timezone.utc)
    report_unclaimed = notifier.format_summary_report(ctx_unclaimed, now_dt=ref_dt)
    assert "Награда за 23.10:</b> ⏳ Ещё не забрана (0/10 — доступна!)" in report_unclaimed
    assert "Сбор ещё не запускался" in report_unclaimed
    assert "всё чётко" not in report_unclaimed

    # Case 2: Claimed accounts with 50+ and 150+ jackpot drops
    c1 = StateMachineContext(
        username="lucky1",
        current_state=CrateState.CLAIMED,
        coins_earned=150,
        final_balance=250,
        status=DailyCrateStatus(streak=7, last_claimed_on="2026-10-23", next_reset_at="2026-10-24T00:00:00Z"),
    )
    c2 = StateMachineContext(
        username="lucky2",
        current_state=CrateState.CLAIMED,
        coins_earned=150,
        final_balance=300,
        status=DailyCrateStatus(streak=14, last_claimed_on="2026-10-23", next_reset_at="2026-10-24T00:00:00Z"),
    )
    c3 = StateMachineContext(
        username="modest3",
        current_state=CrateState.CLAIMED,
        coins_earned=50,
        final_balance=150,
        status=DailyCrateStatus(streak=3, last_claimed_on="2026-10-23", next_reset_at="2026-10-24T00:00:00Z"),
    )
    c4 = StateMachineContext(
        username="regular4",
        current_state=CrateState.CLAIMED,
        coins_earned=25,
        final_balance=75,
        status=DailyCrateStatus(streak=2, last_claimed_on="2026-10-23", next_reset_at="2026-10-24T00:00:00Z"),
    )
    report_claimed = notifier.format_summary_report([c1, c2, c3, c4], now_dt=ref_dt)

    # Status must say 'всё чётко' because all 4/4 were claimed
    assert "Награда за 23.10:</b> ✅ Забрана со всех (4/4)" in report_claimed
    assert "✔ <b>Ошибки:</b> 0 (всё чётко)" in report_claimed
    assert "💰 <b>Общий баланс:</b> 775 монет (13 мин) (+375)" in report_claimed

    # 50+ must be concise (count only, NO usernames listed)
    assert "🎁 <b>Сегодня выпало 50+:</b> 3 акк." in report_claimed
    assert "lucky1 (+150)" not in report_claimed.split("Сегодня выпало 50+:")[1].split("\n")[0]

    # Jackpot count must be concise
    assert "🎰 <b>Сегодня джекпот:</b> 2 акк." in report_claimed

    # 150+ must list the specific accounts and amounts
    assert "💥 <b>Выпало 150+:</b> 2 (<code>lucky1</code> (+150), <code>lucky2</code> (+150))" in report_claimed
    assert "🏆 <b>Топ-аккаунт:</b> <code>lucky2</code> (300 монет, 3 мин)" in report_claimed







