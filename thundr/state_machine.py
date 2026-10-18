"""
Strict Finite State Machine for Thundr Daily Crate claiming.
Provides idempotency, state transition validation, verification of reward credit,
and detailed lifecycle history tracking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from thundr.account import AccountRecord, DailyCrateStatus
from thundr.client import (
    ThundrAuthError,
    ThundrClient,
    ThundrNetworkError,
    ThundrRateLimitError,
)

logger = logging.getLogger("thundr.state_machine")


class CrateState(str, Enum):
    """Lifecycle states for daily crate processing."""
    UNINITIALIZED = "UNINITIALIZED"
    NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
    CHECKING = "CHECKING"
    READY = "READY"
    COOLDOWN = "COOLDOWN"
    ERROR = "ERROR"
    CLAIMING = "CLAIMING"
    VERIFYING = "VERIFYING"
    CLAIMED = "CLAIMED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"


# Valid state transitions
VALID_TRANSITIONS: Dict[CrateState, Set[CrateState]] = {
    CrateState.UNINITIALIZED: {CrateState.NOT_AUTHENTICATED, CrateState.CHECKING, CrateState.ERROR, CrateState.FAILED},
    CrateState.NOT_AUTHENTICATED: {CrateState.CHECKING, CrateState.ERROR, CrateState.FAILED},
    CrateState.CHECKING: {CrateState.READY, CrateState.COOLDOWN, CrateState.ERROR, CrateState.FAILED},
    CrateState.READY: {CrateState.CLAIMING, CrateState.ERROR, CrateState.FAILED},
    CrateState.CLAIMING: {CrateState.VERIFYING, CrateState.COOLDOWN, CrateState.UNCERTAIN, CrateState.ERROR, CrateState.FAILED},
    CrateState.VERIFYING: {CrateState.CLAIMED, CrateState.COOLDOWN, CrateState.UNCERTAIN, CrateState.ERROR, CrateState.FAILED},
    CrateState.ERROR: {CrateState.FAILED},
    CrateState.CLAIMED: set(),      # Terminal
    CrateState.COOLDOWN: set(),     # Terminal
    CrateState.FAILED: set(),       # Terminal
    CrateState.UNCERTAIN: set(),    # Terminal
}


@dataclass
class StateMachineContext:
    """Execution context and result of the state machine run."""
    username: str
    current_state: CrateState = CrateState.NOT_AUTHENTICATED
    state_history: List[Tuple[CrateState, str, str]] = field(default_factory=list)
    initial_balance: Optional[int] = None
    final_balance: Optional[int] = None
    coins_earned: int = 0
    last_reward_coins: Optional[int] = None
    status: Optional[DailyCrateStatus] = None
    claim_result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: Optional[str] = None
    last_checked_at: Optional[str] = None

    @property
    def is_success(self) -> bool:
        return self.current_state in (CrateState.CLAIMED, CrateState.COOLDOWN)

    def record_transition(self, new_state: CrateState, note: str = "") -> None:
        valid_next = VALID_TRANSITIONS.get(self.current_state, set())
        if new_state not in valid_next and self.current_state != new_state:
            logger.warning(
                f"Unexpected transition for '{self.username}': {self.current_state.value} -> {new_state.value}"
            )
        now_iso = datetime.now(timezone.utc).isoformat()
        self.state_history.append((new_state, now_iso, note))
        self.current_state = new_state
        logger.info(f"[{self.username}] State: {new_state.value} ({note})")

    def finish(self) -> None:
        self.finished_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "username": self.username,
            "final_state": self.current_state.value,
            "initial_balance": self.initial_balance,
            "final_balance": self.final_balance,
            "coins_earned": self.coins_earned,
            "last_reward_coins": self.last_reward_coins,
            "streak": self.status.streak if self.status else 0,
            "error_message": self.error_message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "history": [
                {"state": s.value, "time": t, "note": n}
                for s, t, n in self.state_history
            ],
        }


class DailyCrateStateMachine:
    """Orchestrates the lifecycle of checking, claiming, and verifying a daily crate."""

    def __init__(self, client: ThundrClient):
        self.client = client

    def run(self, account: AccountRecord, dry_run: bool = False) -> StateMachineContext:
        """Run the full crate claim lifecycle for the given account."""
        ctx = StateMachineContext(
            username=account.username,
            current_state=CrateState.NOT_AUTHENTICATED,
            last_reward_coins=account.last_reward_coins,
        )
        auth_note = (
            "Session present, validating via API"
            if account.has_session
            else f"Account '{account.username}' has no active session, authentication required"
        )
        ctx.record_transition(CrateState.NOT_AUTHENTICATED, auth_note)

        try:
            # 1. State: CHECKING
            ctx.record_transition(CrateState.CHECKING, "Fetching daily crate status")
            status = self.client.get_crate_status(account)
            ctx.status = status
            ctx.initial_balance = status.coin_balance

            # 2. Check availability
            if not status.available:
                ctx.final_balance = status.coin_balance
                ctx.record_transition(
                    CrateState.COOLDOWN,
                    f"Crate on cooldown until {status.next_reset_at} (claimed: {status.last_claimed_on})"
                )
                ctx.finish()
                return ctx

            # 3. State: READY
            ctx.record_transition(
                CrateState.READY,
                f"Crate is available to claim! Current balance: {status.coin_balance}"
            )

            if dry_run:
                ctx.record_transition(
                    CrateState.COOLDOWN,
                    "Dry run mode: skipped claiming"
                )
                ctx.finish()
                return ctx

            # 4. State: CLAIMING
            ctx.record_transition(CrateState.CLAIMING, "Sending claim request to API")
            try:
                claim_res = self.client.claim_daily_crate(account)
                ctx.claim_result = claim_res
            except ThundrNetworkError as net_err:
                # Network failed while posting claim. Transition to VERIFYING to see if it landed!
                logger.warning(f"Network error during claim call: {net_err}. Verifying if claim applied...")
                ctx.record_transition(
                    CrateState.VERIFYING,
                    "Network error during claim, attempting verification"
                )
                return self._verify_after_ambiguous_claim(account, ctx)

            # Check if API returned already claimed
            if claim_res.get("already_claimed"):
                ctx.final_balance = ctx.initial_balance
                ctx.record_transition(
                    CrateState.COOLDOWN,
                    "API confirmed crate was already claimed"
                )
                ctx.finish()
                return ctx

            # 5. State: VERIFYING
            ctx.record_transition(CrateState.VERIFYING, "Verifying crate status and balance update")
            new_status = self.client.get_crate_status(account)
            ctx.status = new_status
            ctx.final_balance = new_status.coin_balance
            ctx.coins_earned = max(0, new_status.coin_balance - (ctx.initial_balance or 0))

            # Success criteria: crate is now not available, OR coin balance increased
            if not new_status.available or ctx.coins_earned > 0:
                if ctx.coins_earned > 0:
                    ctx.last_reward_coins = ctx.coins_earned
                ctx.record_transition(
                    CrateState.CLAIMED,
                    f"Successfully claimed! Balance: {ctx.final_balance} (+{ctx.coins_earned} coins), Streak: {new_status.streak}"
                )
            else:
                ctx.record_transition(
                    CrateState.UNCERTAIN,
                    "Claim call succeeded but balance did not increase and crate still marked available"
                )

        except (ThundrAuthError, ThundrRateLimitError) as client_err:
            ctx.error_message = f"{type(client_err).__name__}: {client_err}"
            ctx.record_transition(CrateState.ERROR, ctx.error_message)
            ctx.record_transition(CrateState.FAILED, "Execution halted due to client error")
        except Exception as e:
            logger.exception(f"Unhandled error in state machine for '{account.username}': {e}")
            ctx.error_message = f"Unexpected {type(e).__name__}: {e}"
            ctx.record_transition(CrateState.ERROR, ctx.error_message)
            ctx.record_transition(CrateState.FAILED, "Execution halted due to unhandled error")
        finally:
            ctx.finish()

        return ctx

    def _verify_after_ambiguous_claim(self, account: AccountRecord, ctx: StateMachineContext) -> StateMachineContext:
        """Helper to resolve uncertain state when claim request had network drop."""
        try:
            status = self.client.get_crate_status(account)
            ctx.status = status
            ctx.final_balance = status.coin_balance
            ctx.coins_earned = max(0, status.coin_balance - (ctx.initial_balance or 0))

            if not status.available or ctx.coins_earned > 0:
                ctx.record_transition(
                    CrateState.CLAIMED,
                    f"Verified claim succeeded despite initial network timeout! Balance: {ctx.final_balance}"
                )
            else:
                ctx.record_transition(
                    CrateState.UNCERTAIN,
                    "Claim request timed out and crate is still available. Safe to retry later."
                )
        except Exception as e:
            ctx.error_message = f"Could not verify after network error: {e}"
            ctx.record_transition(CrateState.UNCERTAIN, ctx.error_message)

        ctx.finish()
        return ctx
