"""
Telegram notification module for Thundr Daily Runner.
Formats rich messages, handles Europe/Berlin timezone, and supports multiple notification modes.
"""

from __future__ import annotations

import html
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

from thundr.state_machine import CrateState, StateMachineContext

logger = logging.getLogger("thundr.telegram")


class TelegramNotifier:
    """Sends formatted alerts and summary reports to Telegram."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        mode: Optional[str] = None,
        tz_name: Optional[str] = None,
    ):
        self.bot_token = bot_token if bot_token is not None else os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID")
        self.mode = (mode if mode is not None else os.getenv("TELEGRAM_MODE", "all")).lower().strip()
        self.tz_name = tz_name or os.getenv("APP_TIMEZONE", "Europe/Berlin")


        try:
            self.tz = ZoneInfo(self.tz_name)
        except Exception:
            logger.warning(f"Could not load timezone '{self.tz_name}', falling back to UTC.")
            self.tz = timezone.utc

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def _to_local_str(self, iso_str: Optional[str]) -> str:
        """Convert ISO timestamp string to target timezone formatted string."""
        if not iso_str:
            return "N/A"
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            local_dt = dt.astimezone(self.tz)
            return local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
        except Exception:
            return iso_str

    def send_raw_message(self, text: str, parse_mode: Optional[str] = "HTML") -> bool:
        """Send message via Telegram Bot API with fallback for parse errors."""
        if not self.is_configured:
            logger.debug("Telegram credentials not configured; skipping notification.")
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload: Dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                logger.info("Telegram notification sent successfully.")
                return True
            elif resp.status_code == 400 and parse_mode:
                # Potential parse error, retry in plain text
                logger.warning(f"Telegram parse error ({resp.text}), retrying with plain text...")
                plain_payload = {
                    "chat_id": self.chat_id,
                    "text": text.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", ""),
                    "disable_web_page_preview": True,
                }
                retry_resp = requests.post(url, json=plain_payload, timeout=15)
                return retry_resp.status_code == 200
            else:
                logger.error(f"Failed to send Telegram message (HTTP {resp.status_code}): {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Telegram network error: {e}")
            return False

    def should_notify_single(self, ctx: StateMachineContext) -> bool:
        """Determine if a single account run should trigger a notification based on mode."""
        if self.mode == "summary":
            # In summary mode, individual messages are suppressed in favor of batch report
            return False
        if self.mode == "rewards_only":
            return ctx.current_state == CrateState.CLAIMED
        if self.mode == "errors_only":
            return ctx.current_state in (CrateState.FAILED, CrateState.UNCERTAIN)
        # 'all' mode
        return True

    def notify_single_run(self, ctx: StateMachineContext) -> bool:
        """Send notification for a single account execution."""
        if not self.should_notify_single(ctx):
            return False

        safe_user = html.escape(ctx.username)
        now_str = datetime.now(self.tz).strftime("%Y-%m-%d %H:%M:%S %Z")

        if ctx.current_state == CrateState.CLAIMED:
            earned = f"+{ctx.coins_earned}" if ctx.coins_earned > 0 else "0"
            streak_bonus_text = " (Streak Bonus Activated! 🌟)" if ctx.status and ctx.status.streak_bonus else ""
            msg = (
                f"🎁 <b>Thundr Daily Reward Claimed!</b>\n\n"
                f"👤 <b>Account:</b> <code>{safe_user}</code>\n"
                f"💰 <b>Coins:</b> {ctx.final_balance} (<b>{earned}</b> coins)\n"
                f"🔥 <b>Streak:</b> {ctx.status.streak if ctx.status else 1} days{streak_bonus_text}\n"
                f"⏰ <b>Next reset:</b> {self._to_local_str(ctx.status.next_reset_at if ctx.status else None)}\n"
                f"⏱️ <i>Executed at: {now_str}</i>"
            )
        elif ctx.current_state == CrateState.COOLDOWN:
            reset_time = self._to_local_str(ctx.status.next_reset_at if ctx.status else None)
            msg = (
                f"⏳ <b>Thundr Crate on Cooldown</b>\n\n"
                f"👤 <b>Account:</b> <code>{safe_user}</code>\n"
                f"💰 <b>Current Balance:</b> {ctx.initial_balance} coins\n"
                f"🔥 <b>Streak:</b> {ctx.status.streak if ctx.status else 0} days\n"
                f"⏰ <b>Next Available:</b> {reset_time}\n"
                f"⏱️ <i>Checked at: {now_str}</i>"
            )
        elif ctx.current_state == CrateState.UNCERTAIN:
            msg = (
                f"⚠️ <b>Thundr Claim Uncertain</b>\n\n"
                f"👤 <b>Account:</b> <code>{safe_user}</code>\n"
                f"❓ Status: Claim request was sent, but confirmation is pending.\n"
                f"📝 <b>Note:</b> {html.escape(ctx.error_message or 'Network timeout during claim')}\n"
                f"⏱️ <i>Time: {now_str}</i>"
            )
        else:
            # FAILED
            msg = (
                f"❌ <b>Thundr Claim Failed</b>\n\n"
                f"👤 <b>Account:</b> <code>{safe_user}</code>\n"
                f"⚠️ <b>Error:</b> <code>{html.escape(ctx.error_message or 'Unknown error')}</code>\n"
                f"⏱️ <i>Time: {now_str}</i>"
            )

        return self.send_raw_message(msg)

    def notify_summary(self, contexts: List[StateMachineContext]) -> bool:
        """Send a consolidated summary report for a batch of accounts."""
        if not contexts:
            return False

        # In rewards_only or errors_only mode, check if there's anything to report
        if self.mode == "rewards_only" and not any(c.current_state == CrateState.CLAIMED for c in contexts):
            return False
        if self.mode == "errors_only" and not any(c.current_state in (CrateState.FAILED, CrateState.UNCERTAIN) for c in contexts):
            return False

        now_str = datetime.now(self.tz).strftime("%Y-%m-%d %H:%M:%S %Z")
        claimed_count = sum(1 for c in contexts if c.current_state == CrateState.CLAIMED)
        cooldown_count = sum(1 for c in contexts if c.current_state == CrateState.COOLDOWN)
        failed_count = sum(1 for c in contexts if c.current_state in (CrateState.FAILED, CrateState.UNCERTAIN))
        total_coins_earned = sum(c.coins_earned for c in contexts)

        header = (
            f"📊 <b>Thundr Daily Rewards — Batch Summary</b>\n"
            f"📅 <i>{now_str}</i>\n\n"
            f"👥 Total Accounts: <b>{len(contexts)}</b>\n"
            f"🎁 Claimed: <b>{claimed_count}</b> (+{total_coins_earned} coins)\n"
            f"⏳ Cooldown: <b>{cooldown_count}</b>\n"
            f"❌ Failed: <b>{failed_count}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
        )

        lines = []
        for ctx in contexts:
            safe_user = html.escape(ctx.username)
            if ctx.current_state == CrateState.CLAIMED:
                lines.append(
                    f"✅ <code>{safe_user}</code>: +{ctx.coins_earned} coins "
                    f"(bal: {ctx.final_balance}, streak: {ctx.status.streak if ctx.status else 0}🔥)"
                )
            elif ctx.current_state == CrateState.COOLDOWN:
                lines.append(
                    f"⏳ <code>{safe_user}</code>: Cooldown "
                    f"(bal: {ctx.final_balance or ctx.initial_balance}, streak: {ctx.status.streak if ctx.status else 0}🔥)"
                )
            elif ctx.current_state == CrateState.UNCERTAIN:
                lines.append(f"⚠️ <code>{safe_user}</code>: Uncertain (check manually)")
            else:
                lines.append(f"❌ <code>{safe_user}</code>: Error ({html.escape(ctx.error_message or 'failed')[:40]})")

        full_message = header + "\n".join(lines)
        return self.send_raw_message(full_message)
