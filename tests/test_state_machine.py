"""
Unit tests for thundr.state_machine module.
"""

from unittest.mock import MagicMock

import pytest

from thundr.account import AccountRecord, AccountSession, DailyCrateStatus
from thundr.client import (
    ThundrAuthError,
    ThundrClient,
    ThundrNetworkError,
)
from thundr.state_machine import (
    CrateState,
    DailyCrateStateMachine,
)


@pytest.fixture
def mock_client():
    return MagicMock(spec=ThundrClient)


@pytest.fixture
def test_account():
    return AccountRecord(
        username="tester",
        password="pwd",
        session=AccountSession(user_id="uid1", hashed_token="tok1"),
    )


def test_state_machine_claimed_flow(mock_client, test_account):
    # Setup mock returns
    initial_status = DailyCrateStatus(available=True, coin_balance=10, streak=1)
    verified_status = DailyCrateStatus(available=False, coin_balance=20, streak=2)

    mock_client.get_crate_status.side_effect = [initial_status, verified_status]
    mock_client.claim_daily_crate.return_value = {"ok": True, "reward": 10}

    sm = DailyCrateStateMachine(mock_client)
    ctx = sm.run(test_account)

    assert ctx.current_state == CrateState.CLAIMED
    assert ctx.coins_earned == 10
    assert ctx.final_balance == 20
    assert ctx.is_success is True

    # Check state history progression
    states = [h[0] for h in ctx.state_history]
    assert states == [
        CrateState.UNINITIALIZED,
        CrateState.CHECKING,
        CrateState.READY,
        CrateState.CLAIMING,
        CrateState.VERIFYING,
        CrateState.CLAIMED,
    ]


def test_state_machine_cooldown_flow(mock_client, test_account):
    cooldown_status = DailyCrateStatus(
        available=False,
        coin_balance=15,
        streak=3,
        next_reset_at="2026-10-17T00:00:00Z",
    )
    mock_client.get_crate_status.return_value = cooldown_status

    sm = DailyCrateStateMachine(mock_client)
    ctx = sm.run(test_account)

    assert ctx.current_state == CrateState.COOLDOWN
    assert ctx.coins_earned == 0
    assert ctx.final_balance == 15
    assert ctx.is_success is True
    mock_client.claim_daily_crate.assert_not_called()


def test_state_machine_server_already_claimed(mock_client, test_account):
    available_status = DailyCrateStatus(available=True, coin_balance=10)
    mock_client.get_crate_status.return_value = available_status
    mock_client.claim_daily_crate.return_value = {"claimed": False, "already_claimed": True}

    sm = DailyCrateStateMachine(mock_client)
    ctx = sm.run(test_account)

    assert ctx.current_state == CrateState.COOLDOWN
    assert ctx.coins_earned == 0


def test_state_machine_network_drop_recovers_via_verification(mock_client, test_account):
    initial_status = DailyCrateStatus(available=True, coin_balance=10)
    # Claim call raises NetworkError (e.g. timeout waiting for response)
    mock_client.claim_daily_crate.side_effect = ThundrNetworkError("Socket timeout")

    # Verification status check reveals reward WAS applied!
    verified_status = DailyCrateStatus(available=False, coin_balance=25, streak=2)
    mock_client.get_crate_status.side_effect = [initial_status, verified_status]

    sm = DailyCrateStateMachine(mock_client)
    ctx = sm.run(test_account)

    assert ctx.current_state == CrateState.CLAIMED
    assert ctx.coins_earned == 15
    assert ctx.final_balance == 25


def test_state_machine_dry_run(mock_client, test_account):
    initial_status = DailyCrateStatus(available=True, coin_balance=50)
    mock_client.get_crate_status.return_value = initial_status

    sm = DailyCrateStateMachine(mock_client)
    ctx = sm.run(test_account, dry_run=True)

    assert ctx.current_state == CrateState.COOLDOWN
    mock_client.claim_daily_crate.assert_not_called()


def test_state_machine_auth_failure(mock_client, test_account):
    mock_client.get_crate_status.side_effect = ThundrAuthError("Bad credentials")

    sm = DailyCrateStateMachine(mock_client)
    ctx = sm.run(test_account)

    assert ctx.current_state == CrateState.FAILED
    assert "ThundrAuthError" in str(ctx.error_message)
    assert ctx.is_success is False
