"""
Unit tests for thundr.account module.
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from thundr.account import (
    AccountManager,
    AccountRecord,
    AccountSession,
    DailyCrateStatus,
    mask_secret,
)


def test_mask_secret():
    assert mask_secret("") == "<none>"
    assert mask_secret(None) == "<none>"
    assert mask_secret("short") == "***"
    assert mask_secret("1234567890abcdef") == "1234...cdef"
    assert mask_secret("qKih/zYuAkYELzSpDY+nEr/2ek1KuI9/X511SHd5KD8=") == "qKih...KD8="


def test_account_session_serialization():
    session = AccountSession(
        user_id="user-123",
        hashed_token="secret_token_12345",
        email="test@example.com",
    )
    assert session.is_valid
    assert session.token_masked == "secr...2345"

    d = session.to_dict()
    assert d["user_id"] == "user-123"
    assert d["hashed_token"] == "secret_token_12345"
    assert d["email"] == "test@example.com"

    restored = AccountSession.from_dict(d)
    assert restored.user_id == session.user_id
    assert restored.hashed_token == session.hashed_token
    assert restored.email == session.email


def test_daily_crate_status():
    raw = {
        "available": True,
        "coin_balance": 150,
        "last_claimed_on": "2026-10-15",
        "next_reset_at": "2026-10-16T00:00:00Z",
        "next_streak_bonus_at": 7,
        "streak": 5,
        "streak_bonus": True,
        "server_now": "2026-10-15T23:59:00Z",
    }
    status = DailyCrateStatus.from_dict(raw)
    assert status.available is True
    assert status.coin_balance == 150
    assert status.streak == 5
    assert status.streak_bonus is True
    assert status.next_streak_bonus_at == 7

    d = status.to_dict()
    assert d["available"] is True
    assert d["coin_balance"] == 150
    assert d["streak"] == 5


def test_account_record_secrets_hiding():
    session = AccountSession(user_id="u1", hashed_token="super_secret_token")
    rec = AccountRecord(
        username="alice",
        password="alice_password",
        session=session,
    )
    assert rec.has_credentials
    assert rec.has_session

    with_secrets = rec.to_dict(include_secrets=True)
    assert with_secrets["password"] == "alice_password"
    assert with_secrets["session"]["hashed_token"] == "super_secret_token"

    without_secrets = rec.to_dict(include_secrets=False)
    assert "password" not in without_secrets
    assert without_secrets["session"]["hashed_token"] != "super_secret_token"
    assert "..." in without_secrets["session"]["hashed_token"]


def test_account_manager_crud(tmp_path):
    accounts_file = tmp_path / "accounts.json"
    mgr = AccountManager(filepath=accounts_file)
    assert len(mgr.list_accounts()) == 0

    # Add account
    acc1 = mgr.add_account("user1", "pwd1", "id1", "token1", tags=["vip"])
    assert acc1.username == "user1"
    assert len(mgr.list_accounts()) == 1

    # Reload from disk
    mgr2 = AccountManager(filepath=accounts_file)
    assert len(mgr2.list_accounts()) == 1
    loaded = mgr2.get_account("user1")
    assert loaded is not None
    assert loaded.password == "pwd1"
    assert loaded.session is not None
    assert loaded.session.user_id == "id1"

    # Remove account
    assert mgr2.remove_account("user1") is True
    assert len(mgr2.list_accounts()) == 0
    assert mgr2.get_account("user1") is None


def test_account_manager_env_loading(monkeypatch, tmp_path):
    accounts_file = tmp_path / "accounts.json"

    # Test single env var
    monkeypatch.setenv("THUNDR_USERNAME", "env_user")
    monkeypatch.setenv("THUNDR_PASSWORD", "env_pass")
    monkeypatch.setenv("THUNDR_USER_ID", "env_uid")
    monkeypatch.setenv("THUNDR_HASHED_TOKEN", "env_token")

    mgr = AccountManager(filepath=accounts_file)
    acc = mgr.get_account("env_user")
    assert acc is not None
    assert acc.password == "env_pass"
    assert acc.session is not None
    assert acc.session.user_id == "env_uid"

    # Test multi-account JSON env var
    multi_json = json.dumps([
        {"username": "multi1", "password": "p1"},
        {"username": "multi2", "password": "p2", "user_id": "u2", "hashed_token": "t2"}
    ])
    monkeypatch.setenv("THUNDR_ACCOUNTS_JSON", multi_json)

    mgr_multi = AccountManager(filepath=accounts_file)
    assert mgr_multi.get_account("multi1") is not None
    assert mgr_multi.get_account("multi2") is not None
    assert mgr_multi.get_account("env_user") is not None


def test_account_config_and_state_aliases():
    from thundr.account import AccountConfig, AccountState

    assert AccountConfig is AccountRecord
    assert AccountState is DailyCrateStatus

    cfg = AccountConfig(username="bob", password="secretpassword")
    assert cfg.username == "bob"
    assert cfg.has_credentials is True

    state = AccountState(available=True, coin_balance=100)
    assert state.available is True
    assert state.coin_balance == 100

