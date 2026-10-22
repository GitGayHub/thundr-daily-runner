"""
Thundr Daily Rewards Automation Package.
"""

import thundr.env_loader

from thundr.account import (
    AccountConfig,
    AccountManager,
    AccountRecord,
    AccountSession,
    AccountState,
    DailyCrateStatus,
)
from thundr.client import (
    ThundrApiClient,
    ThundrBrowserClient,
    ThundrClient,
)
from thundr.state_machine import (
    CrateState,
    DailyCrateStateMachine,
    StateMachineContext,
)
from thundr.storage import HistoryEntry, StorageManager
from thundr.telegram import (
    TelegramNotifier,
    calculate_boost_minutes,
    format_streak_info,
)

__version__ = "2.4.0"
__all__ = [
    "AccountConfig",
    "AccountManager",
    "AccountRecord",
    "AccountSession",
    "AccountState",
    "DailyCrateStatus",
    "ThundrApiClient",
    "ThundrBrowserClient",
    "ThundrClient",
    "CrateState",
    "DailyCrateStateMachine",
    "StateMachineContext",
    "HistoryEntry",
    "StorageManager",
    "TelegramNotifier",
    "calculate_boost_minutes",
    "format_streak_info",
]
