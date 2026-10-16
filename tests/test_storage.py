"""
Unit tests for thundr.storage module.
"""

from pathlib import Path

import pytest

from thundr.account import DailyCrateStatus
from thundr.state_machine import CrateState, StateMachineContext
from thundr.storage import HistoryEntry, StorageManager


def test_history_entry_from_context():
    ctx = StateMachineContext(
        username="alice",
        current_state=CrateState.CLAIMED,
        initial_balance=10,
        final_balance=35,
        coins_earned=25,
        status=DailyCrateStatus(available=False, coin_balance=35, streak=4, streak_bonus=True),
        started_at="2026-10-16T12:00:00+00:00",
        finished_at="2026-10-16T12:00:02+00:00",
    )
    entry = HistoryEntry.from_context(ctx)

    assert entry.username == "alice"
    assert entry.final_state == "CLAIMED"
    assert entry.coins_earned == 25
    assert entry.initial_balance == 10
    assert entry.final_balance == 35
    assert entry.streak == 4
    assert entry.streak_bonus is True
    assert entry.duration_seconds == 2.0


def test_storage_manager_recording_and_analytics(tmp_path):
    h_file = tmp_path / "history.json"
    csv_file = tmp_path / "history.csv"

    storage = StorageManager(history_file=h_file, csv_file=csv_file)

    # Record 3 runs
    e1 = HistoryEntry(
        id="1", timestamp="2026-10-15T00:00:00Z", username="user1",
        final_state="CLAIMED", initial_balance=10, final_balance=20,
        coins_earned=10, streak=1, streak_bonus=False
    )
    e2 = HistoryEntry(
        id="2", timestamp="2026-10-16T00:00:00Z", username="user1",
        final_state="CLAIMED", initial_balance=20, final_balance=35,
        coins_earned=15, streak=2, streak_bonus=False
    )
    e3 = HistoryEntry(
        id="3", timestamp="2026-10-16T00:00:00Z", username="user2",
        final_state="COOLDOWN", initial_balance=50, final_balance=50,
        coins_earned=0, streak=5, streak_bonus=False
    )

    storage.record_run(e1)
    storage.record_run(e2)
    storage.record_run(e3)

    # Test get_history
    history = storage.get_history()
    assert len(history) == 3
    # Newest first
    assert history[0].id == "3"

    user1_history = storage.get_history(username="user1")
    assert len(user1_history) == 2

    # Test summary
    summary = storage.get_summary()
    assert summary["total_runs"] == 3
    assert summary["total_coins_earned"] == 25
    assert summary["claimed_count"] == 2
    assert summary["cooldown_count"] == 1
    assert len(summary["accounts"]) == 2

    # Test CSV export
    assert csv_file.exists()
    content = csv_file.read_text(encoding="utf-8")
    assert "user1" in content
    assert "user2" in content
    assert "CLAIMED" in content
    assert "COOLDOWN" in content
