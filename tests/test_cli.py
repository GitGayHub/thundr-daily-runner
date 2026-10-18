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


