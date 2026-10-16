"""
Live integration tests against Thundr API endpoints.
Only run when network connectivity to id.thundr.com is available.
"""

import os
import pytest
from thundr.account import AccountRecord, AccountSession
from thundr.client import ThundrApiClient, ThundrClient
from thundr.state_machine import DailyCrateStateMachine, CrateState


TEST_USER_ID = "ac1ad73b-dd54-4f73-b14e-b164a592eea4"
TEST_HASHED_TOKEN = "qKih/zYuAkYELzSpDY+nEr/2ek1KuI9/X511SHd5KD8="


def test_live_balance_and_crate_status():
    api = ThundrApiClient()
    try:
        balance = api.get_balance(TEST_USER_ID, TEST_HASHED_TOKEN)
        assert isinstance(balance, int)
        assert balance >= 0

        status = api.get_crate_status(TEST_USER_ID, TEST_HASHED_TOKEN)
        assert status is not None
        assert isinstance(status.coin_balance, int)
        assert isinstance(status.available, bool)
        assert status.streak >= 0
        assert status.next_reset_at is not None
        assert "Z" in status.next_reset_at or "+00:00" in status.next_reset_at
    except Exception as e:
        if "Network error" in str(e) or "policy" in str(e) or "403" in str(e) or "Forbidden" in str(e):
            pytest.skip(f"Live network unavailable or policy restricted: {e}")
        raise


def test_live_state_machine_execution():
    api = ThundrApiClient()
    client = ThundrClient(api_client=api)
    account = AccountRecord(
        username="gamasek1",
        session=AccountSession(user_id=TEST_USER_ID, hashed_token=TEST_HASHED_TOKEN),
    )

    sm = DailyCrateStateMachine(client)
    try:
        ctx = sm.run(account)
        # Should be in COOLDOWN (already claimed today) or CLAIMED
        assert ctx.current_state in (CrateState.COOLDOWN, CrateState.CLAIMED)
        assert ctx.initial_balance is not None
        assert ctx.status is not None
    except Exception as e:
        if "Network error" in str(e) or "policy" in str(e) or "403" in str(e) or "Forbidden" in str(e):
            pytest.skip(f"Live network unavailable or policy restricted: {e}")
        raise
