"""
Unit tests for CLI commands in cli.py.
"""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from cli import cli
from thundr.account import AccountRecord, AccountSession, DailyCrateStatus
from thundr.state_machine import CrateState, StateMachineContext


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Thundr Daily Rewards Automation CLI" in result.output


def test_cli_add_and_list_and_remove_account(tmp_path, monkeypatch):
    test_acc_file = tmp_path / "accounts.json"
    monkeypatch.setenv("ACCOUNTS_FILE", str(test_acc_file))
    # Clear any leftover env vars that might interfere
    monkeypatch.delenv("THUNDR_USERNAME", raising=False)
    monkeypatch.delenv("THUNDR_ACCOUNTS_JSON", raising=False)

    runner = CliRunner()

    # Add account
    res = runner.invoke(cli, [
        "add-account",
        "--username", "bob",
        "--password", "secret123",
        "--user-id", "uid-bob",
        "--hashed-token", "token-bob-12345",
    ])
    assert res.exit_code == 0
    assert "added successfully" in res.output

    # List accounts
    res = runner.invoke(cli, ["list-accounts"])
    assert res.exit_code == 0
    assert "bob" in res.output
    # Secrets should not be exposed in raw form
    assert "secret123" not in res.output

    # Remove account
    res = runner.invoke(cli, ["remove-account", "bob"])
    assert res.exit_code == 0
    assert "removed" in res.output


def test_cli_export(tmp_path, monkeypatch):
    h_file = tmp_path / "history.json"
    csv_file = tmp_path / "history.csv"
    json_export = tmp_path / "export.json"

    monkeypatch.setenv("HISTORY_FILE", str(h_file))
    monkeypatch.setenv("CSV_FILE", str(csv_file))

    runner = CliRunner()
    res = runner.invoke(cli, ["export", "--format", "csv"])
    assert res.exit_code == 0
    assert "History exported to CSV" in res.output

    res = runner.invoke(cli, ["export", "--format", "json", "-o", str(json_export)])
    assert res.exit_code == 0
    assert "History exported to JSON" in res.output
    assert json_export.exists()


@patch("cli.ThundrClient")
def test_cli_check(mock_client_cls, tmp_path, monkeypatch):
    test_acc_file = tmp_path / "accounts.json"
    monkeypatch.setenv("ACCOUNTS_FILE", str(test_acc_file))
    monkeypatch.delenv("THUNDR_USERNAME", raising=False)
    monkeypatch.delenv("THUNDR_ACCOUNTS_JSON", raising=False)

    runner = CliRunner()
    # Add account first
    runner.invoke(cli, ["add-account", "-u", "charlie", "-p", "pwd"])

    # Mock get_crate_status
    mock_instance = MagicMock()
    mock_instance.get_crate_status.return_value = DailyCrateStatus(
        available=True, coin_balance=80, streak=4
    )
    mock_client_cls.return_value = mock_instance

    res = runner.invoke(cli, ["check"])
    assert res.exit_code == 0
    assert "charlie" in res.output
    assert "80" in res.output
    assert "YES" in res.output
    assert "Server Game Day:" in res.output


@patch("cli.DailyCrateStateMachine")
def test_cli_run(mock_sm_cls, tmp_path, monkeypatch):
    test_acc_file = tmp_path / "accounts.json"
    monkeypatch.setenv("ACCOUNTS_FILE", str(test_acc_file))
    monkeypatch.delenv("THUNDR_USERNAME", raising=False)
    monkeypatch.delenv("THUNDR_ACCOUNTS_JSON", raising=False)

    runner = CliRunner()
    runner.invoke(cli, ["add-account", "-u", "dave", "-p", "pwd"])

    mock_sm = MagicMock()
    mock_ctx = StateMachineContext(
        username="dave",
        current_state=CrateState.CLAIMED,
        initial_balance=10,
        final_balance=25,
        coins_earned=15,
        status=DailyCrateStatus(streak=3),
    )
    mock_sm.run.return_value = mock_ctx
    mock_sm_cls.return_value = mock_sm

    res = runner.invoke(cli, ["run", "--no-telegram"])
    assert res.exit_code == 0
    assert "dave" in res.output
    assert "CLAIMED" in res.output
    assert "+15" in res.output


@patch("cli.DailyCrateStateMachine")
def test_cli_claim(mock_sm_cls, tmp_path, monkeypatch):
    test_acc_file = tmp_path / "accounts.json"
    monkeypatch.setenv("ACCOUNTS_FILE", str(test_acc_file))
    monkeypatch.delenv("THUNDR_USERNAME", raising=False)
    monkeypatch.delenv("THUNDR_ACCOUNTS_JSON", raising=False)

    runner = CliRunner()
    runner.invoke(cli, ["add-account", "-u", "dave", "-p", "pwd"])

    mock_sm = MagicMock()
    mock_ctx = StateMachineContext(
        username="dave",
        current_state=CrateState.CLAIMED,
        initial_balance=10,
        final_balance=25,
        coins_earned=15,
        status=DailyCrateStatus(streak=3),
    )
    mock_sm.run.return_value = mock_ctx
    mock_sm_cls.return_value = mock_sm

    res = runner.invoke(cli, ["claim", "-a", "dave", "--no-telegram"])
    assert res.exit_code == 0
    assert "dave" in res.output
    assert "CLAIMED" in res.output
    assert "+15" in res.output

    # Positional argument invocation: `cli claim dave`
    res_pos = runner.invoke(cli, ["claim", "dave", "--no-telegram"])
    assert res_pos.exit_code == 0
    assert "dave" in res_pos.output
    assert "CLAIMED" in res_pos.output


@patch("cli.TelegramNotifier")
@patch("cli.DailyCrateStateMachine")
def test_cli_run_with_telegram(mock_sm_cls, mock_tg_cls, tmp_path, monkeypatch):
    """Ensure cli run with Telegram enabled does not raise NameError (e.g. target_accounts)."""
    test_acc_file = tmp_path / "accounts.json"
    monkeypatch.setenv("ACCOUNTS_FILE", str(test_acc_file))
    monkeypatch.delenv("THUNDR_USERNAME", raising=False)
    monkeypatch.delenv("THUNDR_ACCOUNTS_JSON", raising=False)

    mock_notifier = MagicMock()
    mock_notifier.mode = "digest_2x"
    mock_tg_cls.return_value = mock_notifier

    mock_sm = MagicMock()
    mock_ctx = StateMachineContext(
        username="dave",
        current_state=CrateState.CLAIMED,
        initial_balance=10,
        final_balance=25,
        coins_earned=15,
        status=DailyCrateStatus(streak=3),
    )
    mock_sm.run.return_value = mock_ctx
    mock_sm_cls.return_value = mock_sm

    runner = CliRunner()
    runner.invoke(cli, ["add-account", "-u", "dave", "-p", "pwd"])
    runner.invoke(cli, ["add-account", "-u", "alice", "-p", "pwd"])

    # Multi-account run with Telegram
    res_multi = runner.invoke(cli, ["run"])
    assert res_multi.exit_code == 0
    assert mock_notifier.notify_summary.called

    # Single-account run with Telegram (tests len(accounts) == 1 path)
    res_single = runner.invoke(cli, ["run", "-a", "dave"])
    assert res_single.exit_code == 0
    assert mock_notifier.notify_single_run.called


def test_cli_goal(tmp_path, monkeypatch):
    """Test cli goal command tracking."""
    test_acc_file = tmp_path / "accounts.json"
    monkeypatch.setenv("ACCOUNTS_FILE", str(test_acc_file))
    monkeypatch.delenv("THUNDR_USERNAME", raising=False)
    monkeypatch.delenv("THUNDR_ACCOUNTS_JSON", raising=False)

    runner = CliRunner()
    runner.invoke(cli, ["add-account", "-u", "alice", "-p", "pwd"])

    res = runner.invoke(cli, ["goal", "--target", "50", "--no-telegram"])
    assert res.exit_code == 0
    assert "Goal Progress" in res.output
    assert "alice" in res.output


@patch("cli.ThundrClient")
def test_cli_boost(mock_client_cls, tmp_path, monkeypatch):
    """Test cli boost command."""
    test_acc_file = tmp_path / "accounts.json"
    monkeypatch.setenv("ACCOUNTS_FILE", str(test_acc_file))
    monkeypatch.delenv("THUNDR_USERNAME", raising=False)
    monkeypatch.delenv("THUNDR_ACCOUNTS_JSON", raising=False)

    runner = CliRunner()
    runner.invoke(cli, ["add-account", "-u", "alice", "-p", "pwd"])

    # List/check boost eligibility
    res = runner.invoke(cli, ["boost", "--type", "mini"])
    assert res.exit_code == 0
    assert "Thundr Boost Manager" in res.output

    # Boost --all with dry-run
    res_all = runner.invoke(cli, ["boost", "--all", "--type", "mini", "--dry-run"])
    assert res_all.exit_code == 0


@patch("cli.DailyCrateStateMachine")
def test_cli_run_account_exception_isolation(mock_sm_cls, tmp_path, monkeypatch):
    """Ensure an unhandled exception on one account does not crash the run for others."""
    test_acc_file = tmp_path / "accounts.json"
    monkeypatch.setenv("ACCOUNTS_FILE", str(test_acc_file))
    monkeypatch.delenv("THUNDR_USERNAME", raising=False)
    monkeypatch.delenv("THUNDR_ACCOUNTS_JSON", raising=False)

    runner = CliRunner()
    runner.invoke(cli, ["add-account", "-u", "failing_user", "-p", "pwd"])
    runner.invoke(cli, ["add-account", "-u", "healthy_user", "-p", "pwd"])

    mock_sm = MagicMock()

    def side_effect(acc, dry_run=False):
        if acc.username == "failing_user":
            raise RuntimeError("Unexpected network disconnect")
        return StateMachineContext(
            username=acc.username,
            current_state=CrateState.CLAIMED,
            final_balance=20,
            coins_earned=10,
        )

    mock_sm.run.side_effect = side_effect
    mock_sm_cls.return_value = mock_sm

    res = runner.invoke(cli, ["run", "--no-telegram"])
    assert res.exit_code == 0
    assert "failing_user" in res.output
    assert "FAILED" in res.output
    assert "healthy_user" in res.output
    assert "CLAIMED" in res.output


def test_cli_summary(tmp_path, monkeypatch):
    """Test cli summary command prints Screenshot 2 report."""
    test_acc_file = tmp_path / "accounts.json"
    monkeypatch.setenv("ACCOUNTS_FILE", str(test_acc_file))
    monkeypatch.delenv("THUNDR_USERNAME", raising=False)
    monkeypatch.delenv("THUNDR_ACCOUNTS_JSON", raising=False)

    runner = CliRunner()
    runner.invoke(cli, ["add-account", "-u", "alice", "-p", "pwd"])

    res = runner.invoke(cli, ["summary", "--no-telegram"])
    assert res.exit_code == 0
    assert "THUNDR REPORT — СВОДКА" in res.output
    assert "Всего аккаунтов: <b>1</b>" in res.output


@patch("cli.TelegramNotifier")
def test_cli_goal_and_boost_send_executive_summary(mock_notifier_cls, tmp_path, monkeypatch):
    """Verify that cli goal and boost send Screenshot 2 executive summary to Telegram."""
    test_acc_file = tmp_path / "accounts.json"
    monkeypatch.setenv("ACCOUNTS_FILE", str(test_acc_file))
    monkeypatch.delenv("THUNDR_USERNAME", raising=False)
    monkeypatch.delenv("THUNDR_ACCOUNTS_JSON", raising=False)

    runner = CliRunner()
    runner.invoke(cli, ["add-account", "-u", "alice", "-p", "pwd"])

    mock_notifier = MagicMock()
    mock_notifier.is_configured = True
    mock_notifier.format_summary_report.return_value = "📊 THUNDR REPORT — СВОДКА"
    mock_notifier_cls.return_value = mock_notifier

    # Test goal command sends summary
    res_goal = runner.invoke(cli, ["goal"])
    assert res_goal.exit_code == 0
    assert mock_notifier.send_raw_message.called
    mock_notifier.send_raw_message.assert_called_with("📊 THUNDR REPORT — СВОДКА")

    mock_notifier.reset_mock()
    # Test boost command sends summary
    res_boost = runner.invoke(cli, ["boost"])
    assert res_boost.exit_code == 0
    assert mock_notifier.send_raw_message.called
    mock_notifier.send_raw_message.assert_called_with("📊 THUNDR REPORT — СВОДКА")


def test_cli_stats_command_display(tmp_path, monkeypatch):
    test_acc_file = tmp_path / "accounts.json"
    monkeypatch.setenv("ACCOUNTS_FILE", str(test_acc_file))
    monkeypatch.delenv("THUNDR_USERNAME", raising=False)
    monkeypatch.delenv("THUNDR_ACCOUNTS_JSON", raising=False)

    runner = CliRunner()
    runner.invoke(cli, ["add-account", "-u", "gamasek1", "-p", "pwd"])

    res = runner.invoke(cli, ["stats", "--no-telegram"])
    assert res.exit_code == 0
    assert "ДЕТАЛЬНАЯ СТАТИСТИКА ПО АККАУНТАМ" in res.output
    assert "gamasek1" in res.output


def test_cli_stats_command_request_next(tmp_path, monkeypatch):
    test_state_file = tmp_path / "telegram_state.json"
    monkeypatch.setenv("TELEGRAM_STATE_FILE", str(test_state_file))

    runner = CliRunner()
    res = runner.invoke(cli, ["stats", "--request-next"])
    assert res.exit_code == 0
    assert "Flag 'stats_requested' set to True" in res.output

    from thundr.telegram import TelegramNotifier
    notifier = TelegramNotifier()
    assert notifier.is_stats_requested() is True


@patch("cli.DailyCrateStateMachine")
@patch("cli.TelegramNotifier")
def test_cli_run_consumes_stats_requested(mock_notifier_cls, mock_sm_cls, tmp_path, monkeypatch):
    test_acc_file = tmp_path / "accounts.json"
    test_state_file = tmp_path / "telegram_state.json"
    monkeypatch.setenv("ACCOUNTS_FILE", str(test_acc_file))
    monkeypatch.setenv("TELEGRAM_STATE_FILE", str(test_state_file))
    monkeypatch.delenv("THUNDR_USERNAME", raising=False)
    monkeypatch.delenv("THUNDR_ACCOUNTS_JSON", raising=False)

    runner = CliRunner()
    runner.invoke(cli, ["add-account", "-u", "gamasek1", "-p", "pwd"])

    mock_sm = MagicMock()
    mock_sm.run.return_value = StateMachineContext(
        username="gamasek1",
        current_state=CrateState.COOLDOWN,
        initial_balance=50,
        final_balance=50,
    )
    mock_sm_cls.return_value = mock_sm

    mock_notifier = MagicMock()
    mock_notifier.is_configured = True
    # Case 1: stats_requested is True
    mock_notifier.is_stats_requested.return_value = True
    mock_notifier_cls.return_value = mock_notifier

    res = runner.invoke(cli, ["run"])
    assert res.exit_code == 0
    assert mock_notifier.notify_summary.called
    assert mock_notifier.send_stats_breakdown.called
    assert mock_notifier.clear_stats_requested.called

    # Case 2: stats_requested is False
    mock_notifier.reset_mock()
    mock_notifier.is_stats_requested.return_value = False

    res2 = runner.invoke(cli, ["run"])
    assert res2.exit_code == 0
    assert mock_notifier.notify_summary.called
    assert not mock_notifier.send_stats_breakdown.called


@patch("cli.DailyCrateStateMachine")
@patch("cli.TelegramNotifier")
def test_cli_run_stats_requested_includes_all_accounts_even_single_target(mock_notifier_cls, mock_sm_cls, tmp_path, monkeypatch):
    test_acc_file = tmp_path / "accounts.json"
    test_state_file = tmp_path / "telegram_state.json"
    monkeypatch.setenv("ACCOUNTS_FILE", str(test_acc_file))
    monkeypatch.setenv("TELEGRAM_STATE_FILE", str(test_state_file))
    monkeypatch.delenv("THUNDR_USERNAME", raising=False)
    monkeypatch.delenv("THUNDR_ACCOUNTS_JSON", raising=False)

    runner = CliRunner()
    runner.invoke(cli, ["add-account", "-u", "gamasek1", "-p", "pwd"])
    runner.invoke(cli, ["add-account", "-u", "gamasek2", "-p", "pwd"])

    mock_sm = MagicMock()
    mock_sm.run.return_value = StateMachineContext(
        username="gamasek1",
        current_state=CrateState.CLAIMED,
        initial_balance=50,
        final_balance=60,
    )
    mock_sm_cls.return_value = mock_sm

    mock_notifier = MagicMock()
    mock_notifier.is_configured = True
    mock_notifier.is_stats_requested.return_value = True
    mock_notifier_cls.return_value = mock_notifier

    # Execute for gamasek1 only
    res = runner.invoke(cli, ["run", "gamasek1"])
    assert res.exit_code == 0
    assert mock_notifier.send_stats_breakdown.called
    # Extract passed items
    call_args = mock_notifier.send_stats_breakdown.call_args[0][0]
    usernames = [getattr(it, "username", "") for it in call_args]
    assert "gamasek1" in usernames
    assert "gamasek2" in usernames
    assert mock_notifier.clear_stats_requested.called


def test_cli_stats_minutes_display(tmp_path, monkeypatch):
    """Verify stats CLI command shows boost minutes breakdown."""
    test_acc_file = tmp_path / "accounts.json"
    monkeypatch.setenv("ACCOUNTS_FILE", str(test_acc_file))
    monkeypatch.delenv("THUNDR_USERNAME", raising=False)
    monkeypatch.delenv("THUNDR_ACCOUNTS_JSON", raising=False)

    runner = CliRunner()
    runner.invoke(cli, ["add-account", "-u", "gamasek1", "-p", "pwd"])

    res = runner.invoke(cli, ["stats", "--no-telegram"])
    assert res.exit_code == 0
    assert "ДЕТАЛЬНАЯ СТАТИСТИКА ПО АККАУНТАМ (МИНУТЫ БУСТА)" in res.output
    assert "gamasek1" in res.output
    assert "мин" in res.output


def test_cli_stats_coins_command_and_aliases(tmp_path, monkeypatch):
    """Verify stats-coins and aliases show exact coins breakdown."""
    test_acc_file = tmp_path / "accounts.json"
    monkeypatch.setenv("ACCOUNTS_FILE", str(test_acc_file))
    monkeypatch.delenv("THUNDR_USERNAME", raising=False)
    monkeypatch.delenv("THUNDR_ACCOUNTS_JSON", raising=False)

    runner = CliRunner()
    runner.invoke(cli, ["add-account", "-u", "gamasek1", "-p", "pwd"])

    # 1. stats-coins
    res1 = runner.invoke(cli, ["stats-coins", "--no-telegram"])
    assert res1.exit_code == 0
    assert "gamasek1" in res1.output
    assert "монет" in res1.output

    # 2. stats_coins alias
    res2 = runner.invoke(cli, ["stats_coins", "--no-telegram"])
    assert res2.exit_code == 0
    assert "gamasek1" in res2.output

    # 3. coins alias
    res3 = runner.invoke(cli, ["coins", "--no-telegram"])
    assert res3.exit_code == 0
    assert "gamasek1" in res3.output


def test_cli_stats_coins_request_next(tmp_path, monkeypatch):
    """Verify stats-coins --request-next sets stats_coins_requested flag."""
    test_state_file = tmp_path / "telegram_state.json"
    monkeypatch.setenv("TELEGRAM_STATE_FILE", str(test_state_file))

    runner = CliRunner()
    res = runner.invoke(cli, ["stats-coins", "--request-next"])
    assert res.exit_code == 0
    assert "Flag 'stats_coins_requested' set to True" in res.output

    from thundr.telegram import TelegramNotifier
    notifier = TelegramNotifier()
    assert notifier.is_stats_coins_requested() is True


@patch("cli.DailyCrateStateMachine")
@patch("cli.TelegramNotifier")
def test_cli_run_consumes_stats_coins_requested(mock_notifier_cls, mock_sm_cls, tmp_path, monkeypatch):
    """Verify cli run consumes stats_coins_requested, calls send_stats_coins_breakdown, and clears flag."""
    test_acc_file = tmp_path / "accounts.json"
    test_state_file = tmp_path / "telegram_state.json"
    monkeypatch.setenv("ACCOUNTS_FILE", str(test_acc_file))
    monkeypatch.setenv("TELEGRAM_STATE_FILE", str(test_state_file))
    monkeypatch.delenv("THUNDR_USERNAME", raising=False)
    monkeypatch.delenv("THUNDR_ACCOUNTS_JSON", raising=False)

    runner = CliRunner()
    runner.invoke(cli, ["add-account", "-u", "gamasek1", "-p", "pwd"])

    mock_sm = MagicMock()
    mock_sm.run.return_value = StateMachineContext(
        username="gamasek1",
        current_state=CrateState.COOLDOWN,
        initial_balance=50,
        final_balance=50,
    )
    mock_sm_cls.return_value = mock_sm

    mock_notifier = MagicMock()
    mock_notifier.is_configured = True
    mock_notifier.is_stats_requested.return_value = False
    mock_notifier.is_stats_coins_requested.return_value = True
    mock_notifier_cls.return_value = mock_notifier

    res = runner.invoke(cli, ["run"])
    assert res.exit_code == 0
    assert mock_notifier.send_stats_coins_breakdown.called
    assert mock_notifier.clear_stats_coins_requested.called


@patch("cli.ThundrClient")
def test_cli_check_jackpot_display(mock_client_cls, tmp_path, monkeypatch):
    """Verify check command displays jackpot emoji for 7-day streak."""
    test_acc_file = tmp_path / "accounts.json"
    monkeypatch.setenv("ACCOUNTS_FILE", str(test_acc_file))
    monkeypatch.delenv("THUNDR_USERNAME", raising=False)
    monkeypatch.delenv("THUNDR_ACCOUNTS_JSON", raising=False)

    runner = CliRunner()
    runner.invoke(cli, ["add-account", "-u", "charlie", "-p", "pwd"])

    mock_instance = MagicMock()
    mock_instance.get_crate_status.return_value = DailyCrateStatus(
        available=False, coin_balance=80, streak=7
    )
    mock_client_cls.return_value = mock_instance

    res = runner.invoke(cli, ["check"])
    assert res.exit_code == 0
    assert "charlie" in res.output
    assert "🎰" in res.output


@patch("cli.DailyCrateStateMachine")
@patch("cli.TelegramNotifier")
def test_cli_run_consumes_both_stats_and_stats_coins_simultaneously(
    mock_notifier_cls, mock_sm_cls, tmp_path, monkeypatch
):
    """Verify runner handles simultaneous /stats and /stats_coins requests gracefully."""
    test_acc_file = tmp_path / "accounts.json"
    test_state_file = tmp_path / "telegram_state.json"
    monkeypatch.setenv("ACCOUNTS_FILE", str(test_acc_file))
    monkeypatch.setenv("TELEGRAM_STATE_FILE", str(test_state_file))
    monkeypatch.delenv("THUNDR_USERNAME", raising=False)
    monkeypatch.delenv("THUNDR_ACCOUNTS_JSON", raising=False)

    runner = CliRunner()
    runner.invoke(cli, ["add-account", "-u", "gamasek1", "-p", "pwd"])

    mock_sm = MagicMock()
    mock_sm.run.return_value = StateMachineContext(
        username="gamasek1",
        current_state=CrateState.COOLDOWN,
        initial_balance=50,
        final_balance=50,
    )
    mock_sm_cls.return_value = mock_sm

    mock_notifier = MagicMock()
    mock_notifier.is_configured = True
    mock_notifier.is_stats_requested.return_value = True
    mock_notifier.is_stats_coins_requested.return_value = True
    mock_notifier_cls.return_value = mock_notifier

    res = runner.invoke(cli, ["run"])
    assert res.exit_code == 0
    assert mock_notifier.send_stats_breakdown.called or mock_notifier.send_stats_minutes_breakdown.called
    assert mock_notifier.clear_stats_requested.called
    assert mock_notifier.send_stats_coins_breakdown.called
    assert mock_notifier.clear_stats_coins_requested.called


@patch("thundr.launcher.launch_chrome_with_account")
def test_cli_open_command(mock_launch, tmp_path, monkeypatch):
    """Test cli open command launches Chrome for specified account."""
    test_acc_file = tmp_path / "accounts.json"
    monkeypatch.setenv("ACCOUNTS_FILE", str(test_acc_file))
    monkeypatch.delenv("THUNDR_USERNAME", raising=False)
    monkeypatch.delenv("THUNDR_ACCOUNTS_JSON", raising=False)

    runner = CliRunner()
    runner.invoke(cli, ["add-account", "-u", "alice", "-p", "pwd"])

    mock_launch.return_value = True
    res = runner.invoke(cli, ["open", "alice"])
    assert res.exit_code == 0
    assert "Launching Chrome in temporary profile" in res.output
    assert mock_launch.called


def test_cli_open_nonexistent_account(tmp_path, monkeypatch):
    """Test cli open on nonexistent account exits with error."""
    test_acc_file = tmp_path / "accounts.json"
    monkeypatch.setenv("ACCOUNTS_FILE", str(test_acc_file))
    runner = CliRunner()
    res = runner.invoke(cli, ["open", "ghost_account"])
    assert res.exit_code != 0
    assert "not found" in res.output


@patch("subprocess.run")
def test_cli_sync_command_fallback(mock_subproc, tmp_path, monkeypatch):
    """Test cli sync command fallback if gh returns no artifact."""
    mock_subproc.return_value = MagicMock(returncode=1)
    runner = CliRunner()
    res = runner.invoke(cli, ["sync"])
    assert res.exit_code == 0
    assert "Synchronizing accounts from GitHub Actions" in res.output





