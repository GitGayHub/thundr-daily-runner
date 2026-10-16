"""
Unit tests for thundr.client module.
"""

from unittest.mock import MagicMock, patch

import pytest

from thundr.account import AccountRecord, AccountSession, DailyCrateStatus
from thundr.client import (
    ThundrApiClient,
    ThundrApiError,
    ThundrAuthError,
    ThundrBrowserClient,
    ThundrClient,
    ThundrNetworkError,
    ThundrRateLimitError,
)


def test_api_client_url_encoding():
    client = ThundrApiClient(prefer_curl_cffi=False)
    raw_token = "qKih/zYuAkYELzSpDY+nEr/2ek1KuI9/X511SHd5KD8="

    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = (200, {"coin_balance": 50}, '{"coin_balance": 50}')
        balance = client.get_balance("user-id-1", raw_token)

        assert balance == 50
        called_url = mock_req.call_args[0][1]
        assert "qKih%2FzYuAkYELzSpDY%2BnEr%2F2ek1KuI9%2FX511SHd5KD8%3D" in called_url


def test_api_client_get_crate_status_success():
    client = ThundrApiClient(prefer_curl_cffi=False)
    mock_data = {
        "available": True,
        "coin_balance": 25,
        "streak": 3,
        "next_reset_at": "2026-10-17T00:00:00Z",
    }
    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = (200, mock_data, "{}")
        status = client.get_crate_status("u1", "tok")

        assert status.available is True
        assert status.coin_balance == 25
        assert status.streak == 3


def test_api_client_claim_crate_success():
    client = ThundrApiClient(prefer_curl_cffi=False)
    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = (200, {"ok": True, "reward": 10}, "{}")
        res = client.claim_crate("u1", "tok")
        assert res["claimed"] is True


def test_api_client_claim_crate_already_claimed():
    client = ThundrApiClient(prefer_curl_cffi=False)
    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = (409, {"ok": False, "error": "daily_crate_already_claimed"}, "{}")
        res = client.claim_crate("u1", "tok")
        assert res["claimed"] is False
        assert res["already_claimed"] is True


def test_api_client_401_unauthorized():
    client = ThundrApiClient(prefer_curl_cffi=False, max_retries=1)
    with patch("requests.request") as mock_http:
        resp = MagicMock()
        resp.status_code = 401
        resp.text = '{"error": "unauthorized"}'
        mock_http.return_value = resp

        with pytest.raises(ThundrAuthError):
            client.get_balance("u1", "bad_token")


def test_api_client_429_rate_limit():
    client = ThundrApiClient(prefer_curl_cffi=False, max_retries=1)
    with patch("requests.request") as mock_http:
        resp = MagicMock()
        resp.status_code = 429
        resp.text = '{"error": "rate_limited"}'
        mock_http.return_value = resp

        with pytest.raises(ThundrRateLimitError):
            client.get_balance("u1", "tok")


def test_api_client_500_retries_and_raises():
    client = ThundrApiClient(prefer_curl_cffi=False, max_retries=2, retry_delay=0.01)
    with patch("requests.request") as mock_http:
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "Internal Server Error"
        mock_http.return_value = resp

        with pytest.raises(ThundrApiError):
            client.get_balance("u1", "tok")

        assert mock_http.call_count == 2


def test_api_client_network_error_retries():
    client = ThundrApiClient(prefer_curl_cffi=False, max_retries=2, retry_delay=0.01)
    with patch("requests.request", side_effect=Exception("Connection timed out")):
        with pytest.raises(ThundrNetworkError):
            client.get_balance("u1", "tok")


def test_hybrid_client_auto_reauth_on_401():
    mock_api = MagicMock(spec=ThundrApiClient)
    mock_browser = MagicMock(spec=ThundrBrowserClient)

    # First call to get_crate_status raises 401, second call succeeds
    fresh_status = DailyCrateStatus(available=True, coin_balance=30, streak=2)
    mock_api.get_crate_status.side_effect = [
        ThundrAuthError("HTTP 401 Unauthorized"),
        fresh_status,
    ]

    new_session = AccountSession(user_id="u1", hashed_token="new_valid_token")
    mock_browser.login.return_value = new_session

    client = ThundrClient(api_client=mock_api, browser_client=mock_browser)
    account = AccountRecord(
        username="alice",
        password="password123",
        session=AccountSession(user_id="u1", hashed_token="expired_token"),
    )

    result_status = client.get_crate_status(account, auto_reauth=True)

    assert result_status == fresh_status
    assert account.session == new_session
    mock_browser.login.assert_called_once_with("alice", "password123")
    assert mock_api.get_crate_status.call_count == 2
