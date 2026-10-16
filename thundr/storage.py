"""
Storage management for Thundr rewards history and balance tracking.
Supports JSON persistence, CSV export, and statistical analytics.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from thundr.state_machine import StateMachineContext

logger = logging.getLogger("thundr.storage")


@dataclass
class HistoryEntry:
    """Historical record of an account claim execution."""
    id: str
    timestamp: str
    username: str
    final_state: str
    initial_balance: Optional[int]
    final_balance: Optional[int]
    coins_earned: int
    streak: int
    streak_bonus: bool
    error: Optional[str] = None
    duration_seconds: float = 0.0

    @classmethod
    def from_context(cls, ctx: StateMachineContext) -> HistoryEntry:
        # Calculate duration
        duration = 0.0
        try:
            if ctx.started_at and ctx.finished_at:
                t1 = datetime.fromisoformat(ctx.started_at)
                t2 = datetime.fromisoformat(ctx.finished_at)
                duration = round((t2 - t1).total_seconds(), 2)
        except Exception:
            pass

        streak = ctx.status.streak if ctx.status else 0
        streak_bonus = ctx.status.streak_bonus if ctx.status else False

        return cls(
            id=str(uuid.uuid4())[:8],
            timestamp=ctx.finished_at or datetime.now(timezone.utc).isoformat(),
            username=ctx.username,
            final_state=ctx.current_state.value,
            initial_balance=ctx.initial_balance,
            final_balance=ctx.final_balance,
            coins_earned=ctx.coins_earned,
            streak=streak,
            streak_bonus=streak_bonus,
            error=ctx.error_message,
            duration_seconds=duration,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> HistoryEntry:
        return cls(
            id=data.get("id") or str(uuid.uuid4())[:8],
            timestamp=data.get("timestamp") or datetime.now(timezone.utc).isoformat(),
            username=str(data.get("username", "")),
            final_state=str(data.get("final_state", "")),
            initial_balance=data.get("initial_balance"),
            final_balance=data.get("final_balance"),
            coins_earned=int(data.get("coins_earned", 0)),
            streak=int(data.get("streak", 0)),
            streak_bonus=bool(data.get("streak_bonus", False)),
            error=data.get("error"),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
        )


class StorageManager:
    """Manages history records in JSON and CSV format."""

    def __init__(
        self,
        history_file: Optional[str | Path] = None,
        csv_file: Optional[str | Path] = None,
    ):
        self.history_file = Path(history_file or os.getenv("HISTORY_FILE", "data/history.json"))
        self.csv_file = Path(csv_file or os.getenv("CSV_FILE", "data/history.csv"))
        self._entries: List[HistoryEntry] = []
        self._load()

    def _load(self) -> None:
        """Load history from JSON file."""
        self._entries.clear()
        if self.history_file.exists():
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    items = data if isinstance(data, list) else data.get("records", [])
                    for item in items:
                        if isinstance(item, dict):
                            self._entries.append(HistoryEntry.from_dict(item))
                logger.debug(f"Loaded {len(self._entries)} history entries from {self.history_file}")
            except Exception as e:
                logger.error(f"Error loading {self.history_file}: {e}")

    def save(self) -> None:
        """Persist entries to JSON and sync to CSV."""
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.history_file.with_suffix(".tmp")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump([e.to_dict() for e in self._entries], f, indent=2, ensure_ascii=False)
            temp_file.replace(self.history_file)
            self.export_csv()
        except Exception as e:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass
            logger.error(f"Error saving history to {self.history_file}: {e}")
            raise

    def record_run(self, entry_or_context: HistoryEntry | StateMachineContext) -> HistoryEntry:
        """Record a completed execution context or history entry."""
        if isinstance(entry_or_context, StateMachineContext):
            entry = HistoryEntry.from_context(entry_or_context)
        else:
            entry = entry_or_context

        self._entries.append(entry)
        self.save()
        logger.info(
            f"Recorded history for '{entry.username}': State={entry.final_state}, "
            f"CoinsEarned={entry.coins_earned}, Streak={entry.streak}"
        )
        return entry

    def get_history(self, limit: int = 100, username: Optional[str] = None) -> List[HistoryEntry]:
        """Get history entries sorted by most recent first."""
        records = self._entries
        if username:
            key = username.strip().lower()
            records = [r for r in records if r.username.lower() == key]
        # Return newest first
        return list(reversed(records))[:limit]

    def get_summary(self) -> Dict[str, Any]:
        """Calculate high-level performance metrics and statistics."""
        total_runs = len(self._entries)
        total_coins_earned = sum(e.coins_earned for e in self._entries)
        claimed_count = sum(1 for e in self._entries if e.final_state == "CLAIMED")
        cooldown_count = sum(1 for e in self._entries if e.final_state == "COOLDOWN")
        failed_count = sum(1 for e in self._entries if e.final_state == "FAILED")

        # Per account statistics
        per_account: Dict[str, Dict[str, Any]] = {}
        for e in self._entries:
            acc = e.username.lower()
            if acc not in per_account:
                per_account[acc] = {
                    "username": e.username,
                    "runs": 0,
                    "claimed": 0,
                    "total_earned": 0,
                    "latest_balance": e.final_balance,
                    "latest_streak": e.streak,
                    "last_run": e.timestamp,
                }
            item = per_account[acc]
            item["runs"] += 1
            if e.final_state == "CLAIMED":
                item["claimed"] += 1
            item["total_earned"] += e.coins_earned
            if e.final_balance is not None:
                item["latest_balance"] = e.final_balance
            item["latest_streak"] = e.streak
            item["last_run"] = e.timestamp

        return {
            "total_runs": total_runs,
            "total_coins_earned": total_coins_earned,
            "claimed_count": claimed_count,
            "cooldown_count": cooldown_count,
            "failed_count": failed_count,
            "accounts": list(per_account.values()),
        }

    def export_csv(self, filepath: Optional[str | Path] = None) -> Path:
        """Export history entries to CSV."""
        out_path = Path(filepath or self.csv_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        fieldnames = [
            "id",
            "timestamp",
            "username",
            "final_state",
            "initial_balance",
            "final_balance",
            "coins_earned",
            "streak",
            "streak_bonus",
            "duration_seconds",
            "error",
        ]

        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for entry in self._entries:
                writer.writerow(entry.to_dict())

        logger.debug(f"Exported {len(self._entries)} entries to {out_path}")
        return out_path
