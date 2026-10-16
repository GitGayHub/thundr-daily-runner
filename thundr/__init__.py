"""
Thundr Daily Rewards Automation Package.
"""

from thundr.account import (
    AccountManager,
    AccountRecord,
    AccountSession,
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
from thundr.telegram import TelegramNotifier

__version__ = "2.0.0"
__all__ = [
    "AccountManager",
    "AccountRecord",
    "AccountSession",
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
]
