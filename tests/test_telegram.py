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

