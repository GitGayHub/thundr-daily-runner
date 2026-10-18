"""
Unit tests for thundr.telegram module.
"""

from unittest.mock import MagicMock, patch

import pytest

from thundr.account import DailyCrateStatus
from thundr.state_machine import CrateState, StateMachineContext
from thundr.telegram import TelegramNotifier


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



