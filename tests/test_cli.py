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
