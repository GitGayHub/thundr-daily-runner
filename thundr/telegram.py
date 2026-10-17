"""
Telegram notification module for Thundr Daily Runner.
Formats rich messages, handles Europe/Berlin timezone, and supports multiple notification modes.
"""

from __future__ import annotations

import html
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import thundr.env_loader

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
        self.bot_token = bot_token if bot_token is not None else (
            os.getenv("THUNDR_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
        )
        self.chat_id = chat_id if chat_id is not None else (
            os.getenv("THUNDR_TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
        )
        self.mode = (
            mode
            if mode is not None
            else (os.getenv("THUNDR_TELEGRAM_MODE") or os.getenv("TELEGRAM_MODE", "digest_2x"))
        ).lower().strip()
        self.tz_name = tz_name or os.getenv("APP_TIMEZONE", "Europe/Berlin")
        if os.getenv("TELEGRAM_STATE_FILE"):
            self.state_file = Path(os.environ["TELEGRAM_STATE_FILE"])
        else:
            self.state_file = Path(__file__).resolve().parent.parent / "data" / "telegram_state.json"

        try:
            self.tz = ZoneInfo(self.tz_name)
        except Exception:
            logger.warning(f"Could not load timezone '{self.tz_name}', falling back to UTC.")
            self.tz = timezone.utc

    def _get_last_summary_ts(self) -> float:
        if self.state_file.exists():
            try:
                import json
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                return float(data.get("last_summary_ts", 0))
            except Exception:
                pass
        return 0.0

    def _save_last_summary_ts(self, ts: float) -> None:
        try:
            import json
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps({"last_summary_ts": ts}), encoding="utf-8")
        except Exception:
            pass

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
            return local_dt.strftime("%d.%m %H:%M:%S")
        except Exception:
            return iso_str

    @staticmethod
    def format_time_remaining(iso_str: Optional[str]) -> str:
        """Calculate remaining time until iso_str timestamp as human-readable string."""
        if not iso_str:
            return "неизвестно"
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            diff = dt - now
            total_seconds = int(diff.total_seconds())
            if total_seconds <= 0:
                return "доступен сейчас! ⚡"
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            if hours > 0:
                return f"{hours} ч {minutes} мин"
            else:
                return f"{minutes} мин"
        except Exception:
            return "неизвестно"

    @staticmethod
    def _format_last_reward(coins: Optional[int]) -> str:
        if coins is not None and coins > 0:
            return f"+{coins} монет"
        return "нет данных"

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
        if self.mode in ("summary", "digest_2x", "smart_2x"):
            # In summary/digest modes, individual messages are suppressed in favor of batch report
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
        rem_str = self.format_time_remaining(ctx.status.next_reset_at if ctx.status else None)
        reset_time = self._to_local_str(ctx.status.next_reset_at if ctx.status else None)
        last_rew_str = self._format_last_reward(ctx.last_reward_coins)

        if ctx.current_state == CrateState.CLAIMED:
            earned = f"+{ctx.coins_earned}" if ctx.coins_earned > 0 else "0"
            streak_bonus_text = " (Streak Bonus Activated! 🌟)" if ctx.status and ctx.status.streak_bonus else ""
            msg = (
                f"🎁 <b>Thundr — Ежедневная награда получена!</b>\n\n"
                f"👤 <b>Аккаунт:</b> <code>{safe_user}</code>\n"
                f"🎉 <b>Выпало:</b> <b>{earned}</b> монет\n"
                f"💰 <b>Новый баланс:</b> {ctx.final_balance} монет\n"
                f"🔥 <b>Серия (Streak):</b> {ctx.status.streak if ctx.status else 1} дн.{streak_bonus_text}\n"
                f"⏳ <b>До следующего кейса:</b> {rem_str} (в {reset_time})\n"
                f"⏱️ <i>Время проверки: {now_str}</i>"
            )
        elif ctx.current_state == CrateState.COOLDOWN:
            msg = (
                f"⏳ <b>Thundr — Кейс на перезарядке</b>\n\n"
                f"👤 <b>Аккаунт:</b> <code>{safe_user}</code>\n"
                f"💰 <b>Текущий баланс:</b> {ctx.final_balance or ctx.initial_balance} монет\n"
                f"🎁 <b>В прошлый раз выпало:</b> <b>{last_rew_str}</b>\n"
                f"⏳ <b>Осталось до кейса:</b> <b>{rem_str}</b>\n"
                f"⏰ <b>Будет доступен:</b> {reset_time}\n"
                f"🔥 <b>Серия (Streak):</b> {ctx.status.streak if ctx.status else 0} дн.\n"
                f"⏱️ <i>Время проверки: {now_str}</i>"
            )
        elif ctx.current_state == CrateState.UNCERTAIN:
            msg = (
                f"⚠️ <b>Thundr — Статус не подтверждён</b>\n\n"
                f"👤 <b>Аккаунт:</b> <code>{safe_user}</code>\n"
                f"❓ <i>Запрос на открытие отправлен, но ответ не был подтверждён.</i>\n"
                f"📝 <b>Детали:</b> {html.escape(ctx.error_message or 'Таймаут соединения')}\n"
                f"⏱️ <i>Время: {now_str}</i>"
            )
        else:
            # FAILED
            msg = (
                f"❌ <b>Thundr — Ошибка проверки</b>\n\n"
                f"👤 <b>Аккаунт:</b> <code>{safe_user}</code>\n"
                f"⚠️ <b>Ошибка:</b> <code>{html.escape(ctx.error_message or 'Неизвестная ошибка')}</code>\n"
                f"⏱️ <i>Время: {now_str}</i>"
            )

        return self.send_raw_message(msg)

    def notify_summary(self, contexts: List[StateMachineContext], force: bool = False) -> bool:
        """Send a consolidated summary report for a batch of accounts without flooding chat."""
        if not contexts:
            return False

        has_claimed = any(c.current_state == CrateState.CLAIMED for c in contexts)
        has_errors = any(c.current_state in (CrateState.FAILED, CrateState.UNCERTAIN) for c in contexts)

        # In rewards_only or errors_only mode, check if there's anything to report
        if self.mode == "rewards_only" and not has_claimed:
            return False
        if self.mode == "errors_only" and not has_errors:
            return False

        # In digest_2x mode, throttle pure cooldown summaries to ~2 times per day (every 10 hours)
        if self.mode in ("digest_2x", "smart_2x") and not force:
            if not has_claimed and not has_errors:
                import time
                last_ts = self._get_last_summary_ts()
                now_ts = time.time()
                if last_ts > 0:
                    if (now_ts - last_ts) < (10 * 3600):
                        logger.info("Cooldown summary skipped in digest_2x mode (sent less than 10 hours ago).")
                        return False
                else:
                    # In stateless environment (e.g. GitHub Actions without cached state)
                    # allow cooldown summary only during the 2 daily digest windows
                    if os.getenv("GITHUB_ACTIONS") == "true":
                        now_utc_hour = datetime.now(timezone.utc).hour
                        # Windows around 00:00 and 12:00 UTC (matching 00:05 and 12:05 cron runs)
                        if now_utc_hour not in (0, 1, 12, 13):
                            logger.info(
                                f"Cooldown summary skipped in CI digest_2x mode outside digest window (hour {now_utc_hour} UTC)."
                            )
                            return False

        now_str = datetime.now(self.tz).strftime("%d.%m.%Y %H:%M %Z")
        total_accounts = len(contexts)
        claimed_count = sum(1 for c in contexts if c.current_state == CrateState.CLAIMED)
        cooldown_count = sum(1 for c in contexts if c.current_state == CrateState.COOLDOWN)
        failed_count = sum(1 for c in contexts if c.current_state in (CrateState.FAILED, CrateState.UNCERTAIN))
        total_coins_earned = sum(c.coins_earned for c in contexts)

        # Calculate balances & statistics
        balances = [(c.final_balance or c.initial_balance or 0) for c in contexts]
        total_balance = sum(balances)
        avg_balance = (total_balance / total_accounts) if total_accounts > 0 else 0.0

        # Accounts with 50+ coins
        accs_50_plus = sum(1 for b in balances if b >= 50)

        # Top account by balance
        best_ctx = max(contexts, key=lambda c: (c.final_balance or c.initial_balance or 0)) if contexts else None
        best_user = best_ctx.username if best_ctx else "N/A"
        best_bal = (best_ctx.final_balance or best_ctx.initial_balance or 0) if best_ctx else 0

        # Next reset timing
        next_resets = [c.status.next_reset_at for c in contexts if c.status and c.status.next_reset_at]
        next_reset_ts = min(next_resets) if next_resets else None
        rem_str = self.format_time_remaining(next_reset_ts)

        # 1. Header & Claim status
        if claimed_count == total_accounts:
            claim_line = f"🎁 <b>Сбор наград:</b> Все <b>{total_accounts}</b> собраны! (+{total_coins_earned} монет)"
        elif claimed_count > 0:
            claim_line = f"🎁 <b>Сбор наград:</b> Собрано <b>{claimed_count}/{total_accounts}</b> (+{total_coins_earned} монет)"
        else:
            claim_line = f"⏳ <b>Статус кейсов:</b> Все <b>{total_accounts}</b> на перезарядке"

        # 2. Error line
        if failed_count == 0:
            error_line = "✔ <b>Ошибки:</b> 0 (всё чётко)"
        else:
            error_line = f"❌ <b>Ошибки:</b> {failed_count} акк."

        # Assemble compact message
        msg_parts = [
            f"📊 <b>THUNDR REPORT — СВОДКА</b>",
            f"📅 <i>{now_str}</i>\n",
            f"👥 Всего аккаунтов: <b>{total_accounts}</b>",
            claim_line,
            error_line,
            f"━━━━━━━━━━━━━━━━━━━",
            f"💰 <b>Общий баланс:</b> {total_balance} монет" + (f" (+{total_coins_earned})" if total_coins_earned > 0 else ""),
            f"📊 <b>В среднем на 1 аккаунт:</b> {avg_balance:.1f} монет",
            f"💎 <b>Аккаунтов с 50+ монет:</b> {accs_50_plus} из {total_accounts}",
            f"🏆 <b>Топ-аккаунт:</b> <code>{html.escape(best_user)}</code> ({best_bal} монет)",
            f"━━━━━━━━━━━━━━━━━━━",
            f"⏳ <b>До следующего кейса:</b> {rem_str}",
        ]

        # Only list problematic accounts if any errors occurred
        if failed_count > 0:
            msg_parts.append("\n⚠️ <b>Ошибки по аккаунтам:</b>")
            for c in contexts:
                if c.current_state in (CrateState.FAILED, CrateState.UNCERTAIN):
                    err_msg = html.escape(c.error_message or "ошибка")[:40]
                    msg_parts.append(f"• <code>{html.escape(c.username)}</code>: {err_msg}")

        full_message = "\n".join(msg_parts)
        ok = self.send_raw_message(full_message)
        if ok:
            import time
            self._save_last_summary_ts(time.time())
        return ok

