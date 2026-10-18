"""
Account management, credentials, and session models for Thundr Daily Runner.
Handles multi-account configurations, serialization, and secure masking.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("thundr.account")


def mask_secret(value: Optional[str], visible_start: int = 4, visible_end: int = 4) -> str:
    """Mask sensitive string like tokens and passwords for safe logging."""
    if not value:
        return "<none>"
    length = len(value)
    if length <= visible_start + visible_end:
        return "***"
    return f"{value[:visible_start]}...{value[-visible_end:]}"


@dataclass
class AccountSession:
    """Session credentials obtained from authentication."""
    user_id: str
    hashed_token: str
    email: Optional[str] = None
    last_authenticated: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        return bool(self.user_id and self.hashed_token)

    @property
    def token_masked(self) -> str:
        return mask_secret(self.hashed_token)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "hashed_token": self.hashed_token,
            "email": self.email,
            "last_authenticated": self.last_authenticated or datetime.now(timezone.utc).isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AccountSession:
        return cls(
            user_id=str(data.get("user_id") or data.get("id") or ""),
            hashed_token=str(data.get("hashed_token") or ""),
            email=data.get("email"),
            last_authenticated=data.get("last_authenticated"),
        )


@dataclass
class DailyCrateStatus:
    """Status of the daily crate reward and coin balance."""
    available: bool = False
    coin_balance: int = 0
    last_claimed_on: Optional[str] = None
    next_reset_at: Optional[str] = None
    next_streak_bonus_at: Optional[int] = None
    streak: int = 0
    streak_bonus: bool = False
    server_now: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DailyCrateStatus:
        return cls(
            available=bool(data.get("available", False)),
            coin_balance=int(data.get("coin_balance", 0)),
            last_claimed_on=data.get("last_claimed_on"),
            next_reset_at=data.get("next_reset_at"),
            next_streak_bonus_at=data.get("next_streak_bonus_at"),
            streak=int(data.get("streak", 0)),
            streak_bonus=bool(data.get("streak_bonus", False)),
            server_now=data.get("server_now"),
            raw=data,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "coin_balance": self.coin_balance,
            "last_claimed_on": self.last_claimed_on,
            "next_reset_at": self.next_reset_at,
            "next_streak_bonus_at": self.next_streak_bonus_at,
            "streak": self.streak,
            "streak_bonus": self.streak_bonus,
            "server_now": self.server_now,
        }

    @property
    def is_cooldown(self) -> bool:
        """Return True if daily crate is on cooldown."""
        return not self.available

    def is_claimed_for_cycle(self, cycle_date: str) -> bool:
        """Check if reward was claimed for a specific UTC cycle date (YYYY-MM-DD)."""
        if self.last_claimed_on:
            return str(self.last_claimed_on)[:10] == cycle_date
        if not self.available and self.next_reset_at:
            try:
                rdt = datetime.fromisoformat(self.next_reset_at.replace("Z", "+00:00"))
                return (rdt - timedelta(days=1)).strftime("%Y-%m-%d") == cycle_date
            except Exception:
                pass
        return False


@dataclass
class AccountRecord:
    """Represents a configured user account and its current session state."""
    username: str
    password: Optional[str] = None
    session: Optional[AccountSession] = None
    enabled: bool = True
    tags: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_status: Optional[DailyCrateStatus] = None
    last_checked_at: Optional[str] = None
    last_claimed_at: Optional[str] = None
    last_reward_coins: Optional[int] = None

    @property
    def has_credentials(self) -> bool:
        return bool(self.username and self.password)

    @property
    def has_session(self) -> bool:
        return bool(self.session and self.session.is_valid)

    @property
    def balance(self) -> int:
        if self.last_status and self.last_status.coin_balance is not None:
            return self.last_status.coin_balance
        try:
            from thundr.storage import StorageManager
            bal = StorageManager().get_last_balance_for_user(self.username)
            if bal is not None:
                return bal
        except Exception:
            pass
        return 0

    @property
    def status(self) -> Optional[DailyCrateStatus]:
        return self.last_status

    def to_dict(self, include_secrets: bool = True) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "username": self.username,
            "enabled": self.enabled,
            "tags": list(self.tags),
            "created_at": self.created_at,
            "last_checked_at": self.last_checked_at,
            "last_claimed_at": self.last_claimed_at,
            "last_reward_coins": self.last_reward_coins,
        }
        if include_secrets:
            if self.password:
                result["password"] = self.password
            if self.session:
                result["session"] = self.session.to_dict()
        else:
            if self.session:
                result["session"] = {
                    "user_id": self.session.user_id,
                    "hashed_token": self.session.token_masked,
                    "email": self.session.email,
                }
        if self.last_status:
            result["last_status"] = self.last_status.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AccountRecord:
        session_data = data.get("session")
        session = None
        if isinstance(session_data, dict):
            session = AccountSession.from_dict(session_data)
        elif data.get("user_id") and data.get("hashed_token"):
            session = AccountSession(
                user_id=data["user_id"],
                hashed_token=data["hashed_token"],
                email=data.get("email") or data.get("username"),
            )

        status_data = data.get("last_status")
        last_status = DailyCrateStatus.from_dict(status_data) if isinstance(status_data, dict) else None

        return cls(
            username=str(data.get("username", "")).strip(),
            password=data.get("password"),
            session=session,
            enabled=bool(data.get("enabled", True)),
            tags=list(data.get("tags") or []),
            created_at=data.get("created_at") or datetime.now(timezone.utc).isoformat(),
            last_status=last_status,
            last_checked_at=data.get("last_checked_at"),
            last_claimed_at=data.get("last_claimed_at"),
            last_reward_coins=data.get("last_reward_coins"),
        )


class AccountManager:
    """Manages multi-account storage, loading from environment variables and disk."""

    def __init__(self, filepath: Optional[str | Path] = None):
        default_file = Path(__file__).resolve().parent.parent / "data" / "accounts.json"
        self.filepath = Path(filepath or os.getenv("ACCOUNTS_FILE", default_file))
        self._accounts: Dict[str, AccountRecord] = {}
        self.load()

    def load(self) -> None:
        """Load accounts from environment variables and JSON file."""
        self._accounts.clear()

        # 1. Load from file if exists
        if self.filepath.exists():
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    items = data if isinstance(data, list) else data.get("accounts", [])
                    for item in items:
                        if isinstance(item, dict) and item.get("username"):
                            rec = AccountRecord.from_dict(item)
                            self._accounts[rec.username.lower()] = rec
                logger.info(f"Loaded {len(self._accounts)} account(s) from {self.filepath}")
            except Exception as e:
                logger.error(f"Error reading {self.filepath}: {e}")

        # 2. Check JSON array env var (THUNDR_ACCOUNTS_JSON) - great for CI/CD Secrets
        env_json = os.getenv("THUNDR_ACCOUNTS_JSON")
        if env_json and env_json.strip():
            try:
                items = json.loads(env_json)
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and item.get("username"):
                            rec = AccountRecord.from_dict(item)
                            # Overwrite or merge
                            key = rec.username.lower()
                            if key in self._accounts:
                                existing = self._accounts[key]
                                if rec.password:
                                    existing.password = rec.password
                                if rec.session:
                                    existing.session = rec.session
                                if rec.last_status:
                                    if existing.last_status is None:
                                        existing.last_status = rec.last_status
                                    elif rec.last_status.server_now and existing.last_status.server_now:
                                        if rec.last_status.server_now > existing.last_status.server_now:
                                            existing.last_status = rec.last_status
                                    elif (rec.last_status.last_claimed_on or "") > (existing.last_status.last_claimed_on or ""):
                                        existing.last_status = rec.last_status
                                if rec.last_reward_coins is not None:
                                    if existing.last_reward_coins is None:
                                        existing.last_reward_coins = rec.last_reward_coins
                                if rec.last_claimed_at and not existing.last_claimed_at:
                                    existing.last_claimed_at = rec.last_claimed_at
                                if rec.last_checked_at and not existing.last_checked_at:
                                    existing.last_checked_at = rec.last_checked_at
                            else:
                                self._accounts[key] = rec
                    logger.info(f"Merged accounts from THUNDR_ACCOUNTS_JSON (total: {len(self._accounts)})")
            except Exception as e:
                logger.error(f"Failed to parse THUNDR_ACCOUNTS_JSON: {e}")

        # 3. Check single account environment variables
        env_user = os.getenv("THUNDR_USERNAME")
        env_pass = os.getenv("THUNDR_PASSWORD")
        env_uid = os.getenv("THUNDR_USER_ID")
        env_token = os.getenv("THUNDR_HASHED_TOKEN")

        if env_user:
            key = env_user.strip().lower()
            session = None
            if env_uid and env_token:
                session = AccountSession(
                    user_id=env_uid.strip(),
                    hashed_token=env_token.strip(),
                    email=env_user.strip(),
                )
            if key in self._accounts:
                if env_pass:
                    self._accounts[key].password = env_pass
                if session:
                    self._accounts[key].session = session
            else:
                self._accounts[key] = AccountRecord(
                    username=env_user.strip(),
                    password=env_pass,
                    session=session,
                )
            logger.info(f"Loaded single account '{env_user}' from environment variables")

    def save(self) -> None:
        """Save account records to accounts.json securely."""
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.filepath.with_suffix(".tmp")
        data = [acc.to_dict(include_secrets=True) for acc in self._accounts.values()]
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            temp_file.replace(self.filepath)
            logger.debug(f"Saved {len(self._accounts)} account(s) to {self.filepath}")
        except Exception as e:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass
            logger.error(f"Failed to save accounts to {self.filepath}: {e}")
            raise

    def get_account(self, username: str) -> Optional[AccountRecord]:
        return self._accounts.get(username.strip().lower())

    def list_accounts(self, enabled_only: bool = False) -> List[AccountRecord]:
        accounts = list(self._accounts.values())
        if enabled_only:
            return [a for a in accounts if a.enabled]
        return accounts

    def add_account(
        self,
        username: str,
        password: Optional[str] = None,
        user_id: Optional[str] = None,
        hashed_token: Optional[str] = None,
        tags: Optional[List[str]] = None,
        enabled: bool = True,
    ) -> AccountRecord:
        username = username.strip()
        key = username.lower()
        session = None
        if user_id and hashed_token:
            session = AccountSession(
                user_id=user_id.strip(),
                hashed_token=hashed_token.strip(),
                email=username,
            )
        rec = AccountRecord(
            username=username,
            password=password,
            session=session,
            enabled=enabled,
            tags=tags or [],
        )
        self._accounts[key] = rec
        self.save()
        logger.info(f"Account '{username}' added.")
        return rec

    def remove_account(self, username: str) -> bool:
        key = username.strip().lower()
        if key in self._accounts:
            del self._accounts[key]
            self.save()
            logger.info(f"Account '{username}' removed.")
            return True
        return False

    def update_session(self, username: str, session: AccountSession) -> None:
        key = username.strip().lower()
        if key in self._accounts:
            self._accounts[key].session = session
            self.save()

    def update_status(
        self,
        username: str,
        status: DailyCrateStatus,
        claimed: bool = False,
        reward_coins: Optional[int] = None,
    ) -> None:
        key = username.strip().lower()
        if key in self._accounts:
            acc = self._accounts[key]
            acc.last_status = status
            acc.last_checked_at = datetime.now(timezone.utc).isoformat()
            if claimed:
                acc.last_claimed_at = acc.last_checked_at
            if reward_coins is not None and reward_coins > 0:
                acc.last_reward_coins = reward_coins
            self.save()


# Backward-compatibility domain aliases as specified in requirements
AccountConfig = AccountRecord
AccountState = DailyCrateStatus

__all__ = [
    "mask_secret",
    "AccountSession",
    "DailyCrateStatus",
    "AccountRecord",
    "AccountConfig",
    "AccountState",
    "AccountManager",
]
