"""
Telegram notification module for Thundr Daily Runner.
Formats rich messages, handles Europe/Berlin timezone, and supports multiple notification modes.
"""

from __future__ import annotations

import html
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import thundr.env_loader

import requests

from thundr.state_machine import CrateState, StateMachineContext


logger = logging.getLogger("thundr.telegram")


def calculate_boost_minutes(coins: int) -> int:
    """Calculate the maximum boost minutes obtainable for a coin balance based on store prices.

    Store prices:
    - Mini Boost: 1 min = 100 coins
    - Small Boost: 10 mins = 450 coins
    - Normal Boost: 30 mins = 1000 coins
    - Super Boost: 60 mins = 2100 coins
    """
    if not isinstance(coins, (int, float)) or coins < 100:
        return 0
    coins = int(coins)
    packages = [(100, 1), (450, 10), (1000, 30), (2100, 60)]

    # 1000 coins for 30 mins is the optimal minute-to-coin ratio (0.030 min/coin).
    # For large balances, decompose by taking batches of 1000 coins.
    if coins >= 3000:
        base_30s = (coins - 2000) // 1000
        rem = coins - base_30s * 1000
        return base_30s * 30 + calculate_boost_minutes(rem)

    dp = [0] * (coins + 1)
    for c in range(1, coins + 1):
        best = dp[c - 1]
        for cost, mins in packages:
            if c >= cost:
                cand = dp[c - cost] + mins
                if cand > best:
                    best = cand
        dp[c] = best
    return dp[coins]


def format_streak_info(streak: Any) -> str:
    """Format streak count with slot machine icon for 7-day jackpot and fire icon for regular days."""
    if streak is None:
        return "0 дн."
    try:
        streak_int = int(streak)
    except (ValueError, TypeError):
        return "0 дн."
    if streak_int <= 0:
        return "0 дн."
    icon = "🎰" if streak_int % 7 == 0 else "🔥"
    return f"{icon} {streak_int} дн."


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

    def _get_state(self) -> Dict[str, Any]:
        if self.state_file.exists():
            try:
                import json
                return json.loads(self.state_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_state(self, updates: Dict[str, Any]) -> None:
        try:
            import json
            data = self._get_state()
            data.update(updates)
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _get_last_summary_ts(self) -> float:
        return float(self._get_state().get("last_summary_ts", 0))

    def _save_last_summary_ts(self, ts: float) -> None:
        self._save_state({"last_summary_ts": ts})

    def get_last_update_id(self) -> Optional[int]:
        val = self._get_state().get("last_update_id")
        return int(val) if val is not None else None

    def save_last_update_id(self, update_id: int) -> None:
        self._save_state({"last_update_id": update_id})

    def is_stats_requested(self) -> bool:
        """Check if a /stats breakdown request is currently pending."""
        return bool(self._get_state().get("stats_requested", False))

    def set_stats_requested(self, requested: bool = True) -> None:
        """Persist the stats_requested flag in state storage."""
        self._save_state({"stats_requested": bool(requested)})

    def clear_stats_requested(self) -> None:
        """Clear the stats_requested flag."""
        self.set_stats_requested(False)

    def is_stats_coins_requested(self) -> bool:
        """Check if a /stats_coins breakdown request is currently pending."""
        return bool(self._get_state().get("stats_coins_requested", False))

    def set_stats_coins_requested(self, requested: bool = True) -> None:
        """Persist the stats_coins_requested flag in state storage."""
        self._save_state({"stats_coins_requested": bool(requested)})

    def clear_stats_coins_requested(self) -> None:
        """Clear the stats_coins_requested flag."""
        self.set_stats_coins_requested(False)

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
    def format_time_remaining(iso_str: Optional[str], now_dt: Optional[datetime] = None) -> str:
        """Calculate remaining time until iso_str timestamp as human-readable string."""
        if not iso_str:
            return "неизвестно"
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            now = now_dt if now_dt is not None else datetime.now(timezone.utc)
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

    @staticmethod
    def get_execution_environment_label() -> str:
        """Return honest environment / execution method label."""
        if os.getenv("GITHUB_ACTIONS") == "true":
            return "🌐 Среда: GitHub Actions"
        return "💻 Среда: Local PC"

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

    def notify_single_run(self, ctx: StateMachineContext, force: bool = False) -> bool:
        """Send notification for a single account execution."""
        if not force and not self.should_notify_single(ctx):
            return False

        safe_user = html.escape(ctx.username)
        now_str = datetime.now(self.tz).strftime("%Y-%m-%d %H:%M:%S %Z")
        server_now_dt = None
        if ctx.status and getattr(ctx.status, "server_now", None):
            try:
                server_now_dt = datetime.fromisoformat(ctx.status.server_now.replace("Z", "+00:00"))
            except Exception:
                pass
        rem_str = self.format_time_remaining(ctx.status.next_reset_at if ctx.status else None, now_dt=server_now_dt)
        reset_time = self._to_local_str(ctx.status.next_reset_at if ctx.status else None)
        last_rew_str = self._format_last_reward(ctx.last_reward_coins)

        if ctx.current_state == CrateState.CLAIMED:
            earned = f"+{ctx.coins_earned}" if ctx.coins_earned > 0 else "0"
            streak_bonus_text = " (Streak Bonus Activated! 🌟)" if ctx.status and ctx.status.streak_bonus else ""
            streak_val = ctx.status.streak if ctx.status else 1
            streak_icon = "🎰" if streak_val % 7 == 0 else "🔥"
            msg = (
                f"🎁 <b>Thundr — Ежедневная награда получена!</b>\n\n"
                f"👤 <b>Аккаунт:</b> <code>{safe_user}</code>\n"
                f"🎉 <b>Выпало:</b> <b>{earned}</b> монет\n"
                f"💰 <b>Новый баланс:</b> {ctx.final_balance} монет\n"
                f"{streak_icon} <b>Серия (Streak):</b> {streak_val} дн.{streak_bonus_text}\n"
                f"⏳ <b>До следующего кейса:</b> {rem_str} (в {reset_time})\n"
                f"⏱️ <i>Время проверки: {now_str}</i>"
            )
        elif ctx.current_state == CrateState.COOLDOWN:
            streak_val = ctx.status.streak if ctx.status else 0
            streak_icon = "🎰" if streak_val and streak_val % 7 == 0 else "🔥"
            msg = (
                f"⏳ <b>Thundr — Кейс на перезарядке</b>\n\n"
                f"👤 <b>Аккаунт:</b> <code>{safe_user}</code>\n"
                f"💰 <b>Текущий баланс:</b> {ctx.final_balance or ctx.initial_balance} монет\n"
                f"🎁 <b>В прошлый раз выпало:</b> <b>{last_rew_str}</b>\n"
                f"⏳ <b>Осталось до кейса:</b> <b>{rem_str}</b>\n"
                f"⏰ <b>Будет доступен:</b> {reset_time}\n"
                f"{streak_icon} <b>Серия (Streak):</b> {streak_val} дн.\n"
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

        msg += f"\n{self.get_execution_environment_label()}"
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

        full_message = self.format_summary_report(contexts)
        ok = self.send_raw_message(full_message)
        if ok:
            import time
            self._save_last_summary_ts(time.time())
        return ok

    def _normalize_to_contexts(self, items: List[Any]) -> List[StateMachineContext]:
        """Convert arbitrary AccountRecord or StateMachineContext objects into StateMachineContext."""
        contexts: List[StateMachineContext] = []
        storage = None
        for item in items:
            if isinstance(item, StateMachineContext):
                contexts.append(item)
            else:
                raw_user = getattr(item, "username", "") or (item.get("username", "") if isinstance(item, dict) else "")
                username = raw_user if isinstance(raw_user, str) else str(raw_user)
                status = getattr(item, "status", None) or getattr(item, "last_status", None)
                if isinstance(item, dict):
                    status = item.get("status") or item.get("last_status")

                raw_bal = getattr(item, "balance", 0) if not isinstance(item, dict) else item.get("balance", 0)
                raw_bal = raw_bal or 0
                bal = int(raw_bal) if isinstance(raw_bal, (int, float)) else 0

                raw_last_rew = getattr(item, "last_reward_coins", None) if not isinstance(item, dict) else item.get("last_reward_coins")
                if isinstance(raw_last_rew, (int, float)):
                    last_rew = int(raw_last_rew)
                else:
                    last_rew = None

                if last_rew is None:
                    if storage is None:
                        try:
                            from thundr.storage import StorageManager
                            storage = StorageManager()
                        except Exception:
                            storage = False
                    if storage:
                        last_rew = storage.get_last_reward_for_user(username)

                last_claimed_at = getattr(item, "last_claimed_at", None) or (item.get("last_claimed_at") if isinstance(item, dict) else None)
                last_checked_at = getattr(item, "last_checked_at", None) or (item.get("last_checked_at") if isinstance(item, dict) else None)
                if status is None and last_claimed_at:
                    claimed_on = str(last_claimed_at)[:10]
                    status = DailyCrateStatus(
                        available=False,
                        coin_balance=bal,
                        last_claimed_on=claimed_on,
                    )

                if status and getattr(status, "available", False):
                    state = CrateState.READY
                else:
                    state = CrateState.COOLDOWN

                ctx = StateMachineContext(
                    username=username,
                    current_state=state,
                    initial_balance=bal,
                    final_balance=bal,
                    coins_earned=0,
                    last_reward_coins=last_rew,
                    status=status if hasattr(status, "available") else None,
                    last_checked_at=last_checked_at,
                )
                contexts.append(ctx)
        return contexts

    def determine_game_cycle(
        self, contexts: Optional[List[Any]] = None, now_dt: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Determine the active game server cycle, display date, and next crate timing.

        In Thundr, daily rewards and crates follow the game server's UTC schedule,
        where the daily reset occurs at precisely 00:00:00 UTC.

        Between local midnight (e.g. 00:00 CEST) and server reset (00:00 UTC / 02:00 CEST),
        the local date is ahead of the game server's day. During this window, the
        active/claimed reward cycle belongs to the server's day (e.g. 18.10), while
        the upcoming crate to be unlocked at reset belongs to the new day (e.g. 19.10).
        """
        now_utc = datetime.now(timezone.utc)
        if now_dt is not None:
            ref_dt = now_dt if now_dt.tzinfo else now_dt.replace(tzinfo=timezone.utc)
            now_local = ref_dt.astimezone(self.tz)
            server_now_dt = ref_dt.astimezone(timezone.utc)
        else:
            now_local = datetime.now(self.tz)
            server_now_dt = None
            if contexts:
                for c in contexts:
                    st = getattr(c, "status", None) or getattr(c, "last_status", None)
                    if isinstance(c, dict):
                        st = c.get("status") or c.get("last_status")
                    snow = getattr(st, "server_now", None) if st else (st.get("server_now") if isinstance(st, dict) else None)
                    if snow and isinstance(snow, str):
                        try:
                            parsed_snow = datetime.fromisoformat(snow.replace("Z", "+00:00"))
                            last_chk = getattr(c, "last_checked_at", None)
                            if isinstance(c, dict) and not last_chk:
                                last_chk = c.get("last_checked_at")
                            if last_chk and isinstance(last_chk, str):
                                try:
                                    chk_dt = datetime.fromisoformat(last_chk.replace("Z", "+00:00"))
                                    elapsed = now_utc - chk_dt
                                    if elapsed.total_seconds() > 0:
                                        server_now_dt = parsed_snow + elapsed
                                        break
                                except Exception:
                                    pass
                            server_now_dt = parsed_snow
                            break
                        except Exception:
                            pass

            if server_now_dt is None:
                server_now_dt = now_utc

        local_date_str = now_local.strftime("%Y-%m-%d")
        local_display = now_local.strftime("%d.%m")

        # 2. Extract reset timestamps from contexts
        future_resets = []
        all_resets = []
        if contexts:
            for c in contexts:
                st = getattr(c, "status", None) or getattr(c, "last_status", None)
                if isinstance(c, dict):
                    st = c.get("status") or c.get("last_status")
                nreset = getattr(st, "next_reset_at", None) if st else (st.get("next_reset_at") if isinstance(st, dict) else None)
                if nreset and isinstance(nreset, str):
                    try:
                        rdt = datetime.fromisoformat(nreset.replace("Z", "+00:00"))
                        all_resets.append((rdt, nreset))
                        if rdt > server_now_dt:
                            future_resets.append((rdt, nreset))
                    except Exception:
                        pass

        earliest_future_reset: Optional[datetime] = None
        earliest_future_reset_str: Optional[str] = None
        if future_resets:
            future_resets.sort(key=lambda x: x[0])
            earliest_future_reset, earliest_future_reset_str = future_resets[0]

        # 3. Determine active cycle date and next crate date
        if earliest_future_reset and earliest_future_reset > server_now_dt:
            # Active cycle that was claimed or is in cooldown ends at earliest_future_reset.
            cycle_dt = earliest_future_reset - timedelta(days=1)
            cycle_date_str = cycle_dt.strftime("%Y-%m-%d")
            cycle_display = cycle_dt.strftime("%d.%m")

            # Next crate unlocks at earliest_future_reset (display in local timezone)
            next_crate_local_dt = earliest_future_reset.astimezone(self.tz)
            next_crate_display = next_crate_local_dt.strftime("%d.%m")
            next_reset_iso = earliest_future_reset_str
        else:
            # Server reset has already occurred or no future reset in contexts
            cycle_date_str = server_now_dt.strftime("%Y-%m-%d")
            cycle_display = server_now_dt.strftime("%d.%m")

            # Calculate next reset (upcoming 00:00:00 UTC)
            next_reset_utc = datetime(
                server_now_dt.year, server_now_dt.month, server_now_dt.day, tzinfo=timezone.utc
            ) + timedelta(days=1)
            next_crate_display = next_reset_utc.astimezone(self.tz).strftime("%d.%m")
            if all_resets:
                # If there were past resets, use the latest one so format_time_remaining returns available now
                next_reset_iso = max(all_resets, key=lambda x: x[0])[1]
            else:
                next_reset_iso = next_reset_utc.strftime("%Y-%m-%dT00:00:00Z")

        is_mismatch = (local_date_str != cycle_date_str)

        return {
            "cycle_date_str": cycle_date_str,
            "cycle_display": cycle_display,
            "next_reset_iso": next_reset_iso,
            "next_crate_display": next_crate_display,
            "is_mismatch": is_mismatch,
            "server_now_dt": server_now_dt,
            "local_date_str": local_date_str,
            "local_display": local_display,
        }

    def format_summary_report(self, items: List[Any], now_dt: Optional[datetime] = None) -> str:
        """Format the full executive summary matching Screenshot 2."""
        contexts = self._normalize_to_contexts(items)
        cycle_info = self.determine_game_cycle(contexts, now_dt=now_dt)
        cycle_date_str = cycle_info["cycle_date_str"]
        cycle_display = cycle_info["cycle_display"]
        next_crate_display = cycle_info["next_crate_display"]
        next_reset_ts = cycle_info["next_reset_iso"]
        server_now_dt = cycle_info["server_now_dt"]

        now_local = now_dt.astimezone(self.tz) if (now_dt and now_dt.tzinfo) else (now_dt if now_dt else datetime.now(self.tz))
        if cycle_info.get("is_mismatch"):
            now_str = f"{now_local.strftime('%d.%m.%Y %H:%M %Z')} (сервер: {server_now_dt.strftime('%d.%m %H:%M UTC')})"
        else:
            now_str = now_local.strftime("%d.%m.%Y %H:%M %Z")

        total_accounts = len(contexts)
        claimed_count = sum(1 for c in contexts if c.current_state == CrateState.CLAIMED)
        cooldown_count = sum(1 for c in contexts if c.current_state == CrateState.COOLDOWN)
        failed_count = sum(1 for c in contexts if c.current_state in (CrateState.FAILED, CrateState.UNCERTAIN))
        total_coins_earned = sum(
            int(c.coins_earned) for c in contexts if isinstance(c.coins_earned, (int, float))
        )

        # Calculate balances & statistics
        balances = []
        for c in contexts:
            val = getattr(c, "final_balance", None) or getattr(c, "initial_balance", None)
            if val is None:
                val = getattr(c, "balance", None)
            if val is None and getattr(c, "status", None):
                val = getattr(c.status, "coin_balance", None)
            if val is None and isinstance(c, dict):
                val = c.get("final_balance") or c.get("initial_balance") or c.get("balance") or c.get("coin_balance")
            balances.append(int(val) if isinstance(val, (int, float)) else 0)

        total_balance = sum(balances)
        total_minutes = calculate_boost_minutes(total_balance)
        avg_balance = (total_balance / total_accounts) if total_accounts > 0 else 0.0
        avg_minutes = (total_minutes / total_accounts) if total_accounts > 0 else 0.0

        claimed_today_count = 0
        for c in contexts:
            last_claimed = getattr(c.status, "last_claimed_on", None) if c.status else None
            if not last_claimed and getattr(c, "last_claimed_at", None):
                try:
                    last_claimed = str(c.last_claimed_at)[:10]
                except Exception:
                    pass
            if last_claimed:
                last_claimed = str(last_claimed)[:10]

            if c.current_state == CrateState.CLAIMED:
                claimed_today_count += 1
            elif last_claimed and last_claimed == cycle_date_str:
                claimed_today_count += 1
            elif last_claimed is None and c.current_state == CrateState.COOLDOWN and c.status and not getattr(c.status, "available", False):
                next_reset = getattr(c.status, "next_reset_at", None)
                if next_reset:
                    try:
                        acc_rdt = datetime.fromisoformat(next_reset.replace("Z", "+00:00"))
                        if acc_rdt > server_now_dt:
                            acc_cycle = (acc_rdt - timedelta(days=1)).strftime("%Y-%m-%d")
                            if acc_cycle == cycle_date_str:
                                claimed_today_count += 1
                    except Exception:
                        pass

        if claimed_today_count == total_accounts and total_accounts > 0:
            date_claim_line = f"📅 <b>Награда за {cycle_display}:</b> ✅ Забрана со всех ({total_accounts}/{total_accounts})"
        elif claimed_today_count == 0:
            date_claim_line = f"📅 <b>Награда за {cycle_display}:</b> ⏳ Ещё не забрана (0/{total_accounts} — доступна!)"
        else:
            date_claim_line = f"📅 <b>Награда за {cycle_display}:</b> ⚠️ Забрана частично ({claimed_today_count}/{total_accounts})"

        # Accounts that dropped 50+ coins from the crate
        dropped_50_plus_count = 0
        dropped_150_plus = []
        for c in contexts:
            raw_earned = c.coins_earned if (isinstance(c.coins_earned, (int, float)) and c.coins_earned > 0) else (c.last_reward_coins or 0)
            earned = int(raw_earned) if isinstance(raw_earned, (int, float)) else 0
            if earned >= 50:
                dropped_50_plus_count += 1
            if earned >= 150:
                dropped_150_plus.append((c.username, earned))

        if dropped_50_plus_count > 0:
            drop_50_line = f"🎁 <b>Сегодня выпало 50+:</b> {dropped_50_plus_count} акк."
        else:
            drop_50_line = "🎁 <b>Сегодня выпало 50+:</b> 0"

        # Top account by balance
        best_ctx = max(
            contexts,
            key=lambda c: (
                int(getattr(c, "final_balance", None) or getattr(c, "initial_balance", None) or getattr(c, "balance", None) or 0)
                if isinstance(getattr(c, "final_balance", None) or getattr(c, "initial_balance", None) or getattr(c, "balance", None) or 0, (int, float))
                else 0
            )
        ) if contexts else None
        best_user = str(best_ctx.username) if best_ctx else "N/A"
        raw_bal = (getattr(best_ctx, "final_balance", None) or getattr(best_ctx, "initial_balance", None) or getattr(best_ctx, "balance", None) or 0) if best_ctx else 0
        best_bal = int(raw_bal) if isinstance(raw_bal, (int, float)) else 0
        best_mins = calculate_boost_minutes(best_bal)

        # Jackpot accounts check
        jackpot_accs = []
        for c in contexts:
            st = getattr(c, "status", None)
            streak = getattr(st, "streak", None) if st else None
            if streak is None and isinstance(getattr(c, "last_status", None), dict):
                streak = c.last_status.get("streak")
            elif streak is None and hasattr(getattr(c, "last_status", None), "streak"):
                streak = c.last_status.streak
            if isinstance(streak, (int, float)) and streak > 0:
                streak = int(streak)
                if streak % 7 == 0:
                    jackpot_accs.append((c.username, streak))

        jackpot_lines: List[str] = []
        if jackpot_accs:
            jackpot_lines.append(f"🎰 <b>Сегодня джекпот:</b> {len(jackpot_accs)} акк.")
        if dropped_150_plus:
            dropped_150_details = ", ".join(f"<code>{html.escape(str(u))}</code> (+{amt})" for u, amt in dropped_150_plus)
            jackpot_lines.append(f"💥 <b>Выпало 150+:</b> {len(dropped_150_plus)} ({dropped_150_details})")

        # Next reset timing and next crate display
        rem_str = self.format_time_remaining(next_reset_ts, now_dt=server_now_dt)
        if rem_str == "доступен сейчас! ⚡":
            next_crate_line = f"⏳ <b>До следующего кейса:</b> {rem_str}"
        elif next_crate_display:
            next_crate_line = f"⏳ <b>До следующего кейса (за {next_crate_display}):</b> {rem_str}"
        else:
            next_crate_line = f"⏳ <b>До следующего кейса:</b> {rem_str}"

        # Status / Error line
        if claimed_today_count == total_accounts and total_accounts > 0:
            if failed_count == 0:
                error_line = "✔ <b>Ошибки:</b> 0 (всё чётко)"
            else:
                error_line = f"⚠️ <b>Ошибки:</b> {failed_count} акк."
        elif claimed_today_count == 0:
            if failed_count == 0:
                error_line = f"⏳ <b>Статус:</b> Сбор ещё не запускался (доступно 0/{total_accounts})"
            else:
                error_line = f"❌ <b>Ошибки:</b> {failed_count} акк. (сбор не удался)"
        else:
            if failed_count == 0:
                error_line = f"⚠️ <b>Сбор:</b> Частично ({claimed_today_count}/{total_accounts})"
            else:
                error_line = f"❌ <b>Ошибки:</b> {failed_count} акк. (собрано {claimed_today_count}/{total_accounts})"

        # Assemble compact message matching Screenshot 2 exactly
        msg_parts = [
            f"📊 <b>THUNDR REPORT — СВОДКА</b>",
            f"<i>{now_str}</i>\n",
            f"👥 Всего аккаунтов: <b>{total_accounts}</b>",
            date_claim_line,
            error_line,
            f"━━━━━━━━━━━━━━━━━━━",
            f"💰 <b>Общий баланс:</b> {total_balance} монет ({total_minutes} мин)" + (f" (+{total_coins_earned})" if total_coins_earned > 0 else ""),
            f"📊 <b>В среднем на 1 аккаунт:</b> {avg_balance:.1f} монет ({avg_minutes:.1f} мин)",
            drop_50_line,
            f"🏆 <b>Топ-аккаунт:</b> <code>{html.escape(best_user)}</code> ({best_bal} монет, {best_mins} мин)",
        ]
        if jackpot_lines:
            msg_parts.extend(jackpot_lines)
        msg_parts.extend([
            f"━━━━━━━━━━━━━━━━━━━",
            next_crate_line,
        ])

        # Only list problematic accounts if any errors occurred
        if failed_count > 0:
            msg_parts.append("\n⚠️ <b>Ошибки по аккаунтам:</b>")
            for c in contexts:
                if c.current_state in (CrateState.FAILED, CrateState.UNCERTAIN):
                    err_msg = html.escape(c.error_message or "ошибка")[:40]
                    msg_parts.append(f"• <code>{html.escape(str(c.username))}</code>: {err_msg}")

        msg_parts.append(f"━━━━━━━━━━━━━━━━━━━")
        msg_parts.append(self.get_execution_environment_label())

        return "\n".join(msg_parts)

    @staticmethod
    def _extract_accounts_stats_data(items: List[Any]) -> List[tuple[str, int, Optional[int]]]:
        """Extract sorted list of (username, balance, streak) tuples from diverse input objects."""
        import re

        accounts_data = []
        for item in items:
            username = ""
            bal = None
            streak = None

            if isinstance(item, StateMachineContext):
                username = item.username
                bal = item.final_balance if item.final_balance is not None else item.initial_balance
                if bal is None and item.status:
                    bal = getattr(item.status, "coin_balance", None)
                if bal is None:
                    try:
                        from thundr.account import AccountManager
                        acc_rec = AccountManager().get_account(username)
                        if acc_rec:
                            bal = acc_rec.balance
                    except Exception:
                        pass
                streak = getattr(item.status, "streak", None) if item.status else None
            elif hasattr(item, "username"):
                username = item.username
                bal = getattr(item, "balance", None)
                if bal is None and getattr(item, "last_status", None):
                    bal = getattr(item.last_status, "coin_balance", None)
                streak = getattr(getattr(item, "last_status", None), "streak", None)
            elif isinstance(item, dict):
                username = item.get("username", "")
                bal = item.get("balance") if item.get("balance") is not None else item.get("final_balance")
                if bal is None and isinstance(item.get("last_status"), dict):
                    bal = item["last_status"].get("coin_balance")
                if bal is None:
                    bal = item.get("coin_balance")
                streak = item.get("streak")
                if streak is None and isinstance(item.get("last_status"), dict):
                    streak = item["last_status"].get("streak")
            else:
                continue

            bal = int(bal) if isinstance(bal, (int, float)) else 0
            if streak is not None:
                try:
                    streak = int(streak)
                except Exception:
                    streak = None
            accounts_data.append((username, bal, streak))

        def natural_sort_key(entry):
            return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", str(entry[0]))]

        accounts_data.sort(key=natural_sort_key)
        return accounts_data

    def format_stats_minutes_breakdown(
        self,
        items: List[Any],
        max_chars: int = 3800,
    ) -> List[str]:
        """Format breakdown of accounts and maximum boost minutes obtainable, chunked within Telegram limits."""
        accounts_data = self._extract_accounts_stats_data(items)

        if not accounts_data:
            return [
                "📊 <b>ДЕТАЛЬНАЯ СТАТИСТИКА ПО АККАУНТАМ (МИНУТЫ БУСТА)</b>\n\n"
                "Нет данных об аккаунтах.\n\n"
                f"{self.get_execution_environment_label()}"
            ]

        # Sort accounts descending by obtainable boost minutes, then coins, then natural username
        def minutes_sort_key(entry):
            u, b, s = entry
            mins = calculate_boost_minutes(b)
            user_parts = [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", str(u))]
            return (-mins, -b, user_parts)

        accounts_data = sorted(accounts_data, key=minutes_sort_key)

        total_accounts = len(accounts_data)
        total_balance = sum(b for _, b, _ in accounts_data)
        total_minutes = sum(calculate_boost_minutes(b) for _, b, _ in accounts_data)
        avg_minutes = (total_minutes / total_accounts) if total_accounts > 0 else 0.0

        lines = []
        for username, bal, streak in accounts_data:
            safe_user = html.escape(str(username))
            mins = calculate_boost_minutes(bal)
            streak_extra = ""
            if streak is not None and streak > 0:
                icon = "🎰" if streak % 7 == 0 else "🔥"
                streak_extra = f" {icon} {streak} дн."
            lines.append(f"• <code>{safe_user}</code>: <b>{mins}</b> мин ({bal} монет){streak_extra}")

        env_label = self.get_execution_environment_label()
        base_header = (
            "📊 <b>ДЕТАЛЬНАЯ СТАТИСТИКА ПО АККАУНТАМ (МИНУТЫ БУСТА)</b>\n"
            f"👥 Всего аккаунтов: <b>{total_accounts}</b> | ⏱️ Всего минут: <b>{total_minutes}</b> мин (в ср. {avg_minutes:.1f} мин | 💰 {total_balance} монет)\n"
            "━━━━━━━━━━━━━━━━━━━\n"
        )
        base_footer = f"\n━━━━━━━━━━━━━━━━━━━\n{env_label}"

        # If everything fits in one message, return immediately
        single_msg = base_header + "\n".join(lines) + base_footer
        if len(single_msg) <= max_chars:
            return [single_msg]

        chunks: List[str] = []
        current_lines: List[str] = []
        current_len = 0

        safety_headroom = 350
        max_chunk_content_len = max(500, max_chars - safety_headroom)

        for line in lines:
            line_len = len(line) + 1
            if current_lines and (current_len + line_len > max_chunk_content_len):
                chunks.append("\n".join(current_lines))
                current_lines = [line]
                current_len = line_len
            else:
                current_lines.append(line)
                current_len += line_len

        if current_lines:
            chunks.append("\n".join(current_lines))

        num_parts = len(chunks)
        messages: List[str] = []
        for idx, chunk_text in enumerate(chunks, 1):
            part_header = (
                f"📊 <b>ДЕТАЛЬНАЯ СТАТИСТИКА ПО АККАУНТАМ (МИНУТЫ БУСТА) ({idx}/{num_parts})</b>\n"
                f"👥 Всего аккаунтов: <b>{total_accounts}</b> | ⏱️ Всего минут: <b>{total_minutes}</b> мин (в ср. {avg_minutes:.1f} мин | 💰 {total_balance} монет)\n"
                "━━━━━━━━━━━━━━━━━━━\n"
            )
            part_footer = (
                base_footer
                if idx == num_parts
                else f"\n━━━━━━━━━━━━━━━━━━━\n<i>Продолжение ({idx}/{num_parts})...</i>"
            )
            messages.append(part_header + chunk_text + part_footer)

        return messages

    def format_stats_coins_breakdown(
        self,
        items: List[Any],
        max_chars: int = 3800,
    ) -> List[str]:
        """Format breakdown of accounts and exact coin balances, chunked within Telegram limits."""
        accounts_data = self._extract_accounts_stats_data(items)

        if not accounts_data:
            return [
                "📊 <b>ДЕТАЛЬНАЯ СТАТИСТИКА ПО АККАУНТАМ</b>\n\n"
                "Нет данных об аккаунтах.\n\n"
                f"{self.get_execution_environment_label()}"
            ]

        total_accounts = len(accounts_data)
        total_balance = sum(b for _, b, _ in accounts_data)
        total_minutes = sum(calculate_boost_minutes(b) for _, b, _ in accounts_data)

        lines = []
        for username, bal, streak in accounts_data:
            safe_user = html.escape(str(username))
            streak_extra = ""
            if streak is not None and streak > 0:
                icon = "🎰" if streak % 7 == 0 else "🔥"
                streak_extra = f" ({icon} {streak} дн.)"
            lines.append(f"• <code>{safe_user}</code>: <b>{bal}</b> монет{streak_extra}")

        env_label = self.get_execution_environment_label()
        base_header = (
            "📊 <b>ДЕТАЛЬНАЯ СТАТИСТИКА ПО АККАУНТАМ</b>\n"
            f"👥 Всего аккаунтов: <b>{total_accounts}</b> | 💰 Общий баланс: <b>{total_balance}</b> монет ({total_minutes} мин)\n"
            "━━━━━━━━━━━━━━━━━━━\n"
        )
        base_footer = f"\n━━━━━━━━━━━━━━━━━━━\n{env_label}"

        single_msg = base_header + "\n".join(lines) + base_footer
        if len(single_msg) <= max_chars:
            return [single_msg]

        chunks: List[str] = []
        current_lines: List[str] = []
        current_len = 0

        safety_headroom = 350
        max_chunk_content_len = max(500, max_chars - safety_headroom)

        for line in lines:
            line_len = len(line) + 1
            if current_lines and (current_len + line_len > max_chunk_content_len):
                chunks.append("\n".join(current_lines))
                current_lines = [line]
                current_len = line_len
            else:
                current_lines.append(line)
                current_len += line_len

        if current_lines:
            chunks.append("\n".join(current_lines))

        num_parts = len(chunks)
        messages: List[str] = []
        for idx, chunk_text in enumerate(chunks, 1):
            part_header = (
                f"📊 <b>ДЕТАЛЬНАЯ СТАТИСТИКА ПО АККАУНТАМ ({idx}/{num_parts})</b>\n"
                f"👥 Всего аккаунтов: <b>{total_accounts}</b> | 💰 Общий баланс: <b>{total_balance}</b> монет ({total_minutes} мин)\n"
                "━━━━━━━━━━━━━━━━━━━\n"
            )
            part_footer = (
                base_footer
                if idx == num_parts
                else f"\n━━━━━━━━━━━━━━━━━━━\n<i>Продолжение ({idx}/{num_parts})...</i>"
            )
            messages.append(part_header + chunk_text + part_footer)

        return messages

    def format_stats_breakdown(
        self,
        items: List[Any],
        max_chars: int = 3800,
        mode: str = "coins",
    ) -> List[str]:
        """Format breakdown of accounts in either 'coins' (default) or 'minutes' mode."""
        if mode == "minutes":
            return self.format_stats_minutes_breakdown(items, max_chars=max_chars)
        return self.format_stats_coins_breakdown(items, max_chars=max_chars)

    def send_stats_minutes_breakdown(self, items: List[Any]) -> bool:
        """Send formatted account boost minutes breakdown messages to Telegram."""
        if not self.is_configured:
            logger.debug("Telegram credentials not configured; skipping minutes stats breakdown.")
            return False

        messages = self.format_stats_minutes_breakdown(items)
        all_ok = True
        for msg in messages:
            ok = self.send_raw_message(msg)
            if not ok:
                all_ok = False
        return all_ok

    def send_stats_coins_breakdown(self, items: List[Any]) -> bool:
        """Send formatted account exact coin breakdown messages to Telegram."""
        if not self.is_configured:
            logger.debug("Telegram credentials not configured; skipping coins stats breakdown.")
            return False

        messages = self.format_stats_coins_breakdown(items)
        all_ok = True
        for msg in messages:
            ok = self.send_raw_message(msg)
            if not ok:
                all_ok = False
        return all_ok

    def send_stats_breakdown(self, items: List[Any], mode: str = "minutes") -> bool:
        """Send formatted account stats breakdown messages to Telegram."""
        if mode == "coins":
            return self.send_stats_coins_breakdown(items)
        return self.send_stats_minutes_breakdown(items)

    def get_updates(self, offset: Optional[int] = None, timeout: int = 5) -> List[Dict[str, Any]]:
        """Fetch incoming Telegram updates via long polling."""
        if not self.is_configured:
            return []
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        params: Dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        try:
            resp = requests.get(url, params=params, timeout=timeout + 5)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    return data.get("result", [])
        except Exception as e:
            logger.debug(f"Failed to fetch Telegram updates: {e}")
        return []

    def handle_command(
        self,
        command: str,
        accounts: Optional[List[Any]] = None,
        client: Optional[Any] = None,
    ) -> Optional[str]:
        """Process incoming slash commands (/goal, /boost, /status, /help, /start)."""
        raw_cmd = command.strip()
        cmd = raw_cmd.lower()
        parts = raw_cmd.split()
        if not parts:
            return None

        base_cmd = parts[0].lower()

        if (
            base_cmd.startswith("/goal")
            or base_cmd.startswith("/summary")
            or base_cmd.startswith("/report")
            or base_cmd in ("/svodka", "/сводка")
        ):
            if not accounts:
                return "📊 <b>THUNDR REPORT — СВОДКА</b>\n\nНет данных об аккаунтах."

            # Check if specific account was passed: /goal <username>
            arg = parts[1].strip() if len(parts) > 1 else None
            specific_acc = None

            if arg and not arg.isdigit():
                target_name = arg.lower()
                for a in accounts:
                    if getattr(a, "username", "").lower() == target_name:
                        specific_acc = a
                        break

            if specific_acc:
                bal = getattr(specific_acc, "balance", 0) or 0
                target = 50
                pct = min(100.0, (bal / target * 100)) if target > 0 else 100.0
                if bal >= target:
                    res_line = f"✅ Цель достигнута! (баланс: <b>{bal}</b> монет)"
                else:
                    needed = target - bal
                    res_line = f"⏳ До цели осталось <b>{needed}</b> монет (баланс: <b>{bal}/{target}</b>, {pct:.0f}%)"
                streak_val = getattr(getattr(specific_acc, "status", None), "streak", 0)
                streak_icon = "🎰" if streak_val and streak_val % 7 == 0 else "🔥"
                return (
                    f"🎯 <b>THUNDR GOAL — {html.escape(specific_acc.username)}</b>\n\n"
                    f"{res_line}\n"
                    f"{streak_icon} Стрик: <b>{streak_val or 0} дн.</b>\n\n"
                    f"{self.get_execution_environment_label()}"
                )

            # Return the full executive summary from Screenshot 2!
            return self.format_summary_report(accounts)

        elif base_cmd.startswith("/boost"):
            cost_map = {"mini": 100, "small": 450, "normal": 1000, "super": 2100}
            ready_mini = sum(1 for a in accounts if (getattr(a, "balance", 0) or 0) >= 100) if accounts else 0

            # Subcommand: /boost buy <username> [type]
            if len(parts) >= 2:
                is_buy = parts[1].lower() in ("buy", "activate", "spend")
                if is_buy:
                    user_arg = parts[2] if len(parts) > 2 else None
                    boost_type = parts[3].lower() if len(parts) > 3 else "mini"
                else:
                    user_arg = parts[1]
                    boost_type = parts[2].lower() if len(parts) > 2 else "mini"

                if not user_arg:
                    return f"⚠️ Укажите имя аккаунта: <code>/boost buy &lt;логин&gt; [тип]</code>\n\n{self.get_execution_environment_label()}"

                if boost_type not in cost_map:
                    return f"❌ Неверный тип буста '{html.escape(boost_type)}'. Доступны: mini, small, normal, super.\n\n{self.get_execution_environment_label()}"

                matched_acc = None
                if accounts:
                    for a in accounts:
                        if getattr(a, "username", "").lower() == user_arg.lower():
                            matched_acc = a
                            break

                if not matched_acc:
                    return f"❌ Аккаунт '<code>{html.escape(user_arg)}</code>' не найден.\n\n{self.get_execution_environment_label()}"

                cost = cost_map[boost_type]
                bal = getattr(matched_acc, "balance", 0) or 0

                if not is_buy:
                    # Info about account boost status without spending coins
                    status_str = "✅ Доступен к покупке!" if bal >= cost else f"❌ Не хватает {cost - bal} монет"
                    return (
                        f"⚡ <b>Буст для {html.escape(matched_acc.username)}:</b>\n\n"
                        f"💰 Баланс: <b>{bal}</b> монет\n"
                        f"📦 Тип: <b>{boost_type.upper()}</b> (цена: {cost})\n"
                        f"Статус: {status_str}\n\n"
                        f"💡 Для покупки отправьте: <code>/boost buy {matched_acc.username} {boost_type}</code>\n\n"
                        f"{self.get_execution_environment_label()}"
                    )

                # Attempt purchase
                if bal < cost:
                    return (
                        f"❌ Недостаточно монет у <code>{html.escape(matched_acc.username)}</code>: "
                        f"баланс <b>{bal}</b>, требуется <b>{cost}</b> для {boost_type}.\n\n"
                        f"{self.get_execution_environment_label()}"
                    )

                if client is None:
                    try:
                        from thundr.client import ThundrApiClient, ThundrBrowserClient, ThundrClient
                        client = ThundrClient(
                            ThundrApiClient(),
                            ThundrBrowserClient(headless=True),
                        )
                    except Exception as e:
                        return f"❌ Ошибка инициализации клиента: {e}\n\n{self.get_execution_environment_label()}"

                try:
                    res = client.spend_boost(matched_acc, boost=boost_type)
                    if getattr(matched_acc, "last_status", None) and hasattr(matched_acc.last_status, "coin_balance"):
                        cur_bal = getattr(matched_acc.last_status, "coin_balance", 0)
                        if isinstance(cur_bal, (int, float)):
                            matched_acc.last_status.coin_balance = max(0, int(cur_bal) - cost)
                    return (
                        f"✅ <b>Буст {boost_type.upper()} успешно активирован!</b>\n\n"
                        f"👤 Аккаунт: <code>{html.escape(matched_acc.username)}</code>\n"
                        f"💰 Списано: <b>{cost}</b> монет\n"
                        f"🎉 Результат: {html.escape(str(res.get('message', 'Успешно активирован!')))}\n\n"
                        f"{self.get_execution_environment_label()}"
                    )
                except Exception as e:
                    return f"❌ Ошибка при покупке буста для <code>{html.escape(matched_acc.username)}</code>: {html.escape(str(e))}\n\n{self.get_execution_environment_label()}"

            # General boost store display
            eligible_accs = [
                f"{a.username} ({getattr(a, 'balance', 0)})"
                for a in (accounts or [])
                if (getattr(a, "balance", 0) or 0) >= 100
            ]
            eligible_str = ", ".join(f"<code>{html.escape(n)}</code>" for n in eligible_accs) if eligible_accs else "нет"

            return (
                f"⚡ <b>THUNDR BOOST STORE — МАГАЗИН БУСТОВ</b>\n\n"
                f"• <b>Mini Boost:</b> 100 монет (1 мин)\n"
                f"• <b>Small Boost:</b> 450 монет (10 мин)\n"
                f"• <b>Normal Boost:</b> 1000 монет (30 мин)\n"
                f"• <b>Super Boost:</b> 2100 монет (1 час / 60 мин)\n\n"
                f"💎 Аккаунтов с балансом >= 100 монет (Mini): <b>{ready_mini}</b>\n"
                f"👤 Готовы к бусту: {eligible_str}\n\n"
                f"💡 <i>Для покупки отправьте:</i> <code>/boost buy &lt;логин&gt; [mini]</code>\n\n"
                f"{self.get_execution_environment_label()}"
            )

        elif base_cmd.startswith("/status") or base_cmd.startswith("/check"):
            if not accounts:
                return f"🔍 Нет данных об аккаунтах.\n\n{self.get_execution_environment_label()}"
            ready_accs = [a for a in accounts if getattr(getattr(a, "status", None), "available", False)]
            ready = len(ready_accs)
            total = len(accounts)

            cycle_info = self.determine_game_cycle(accounts)
            server_now_dt = cycle_info["server_now_dt"]

            # Earliest reset
            next_resets = [
                a.status.next_reset_at
                for a in accounts
                if getattr(a, "status", None) and a.status.next_reset_at
            ]
            earliest_reset = min(next_resets) if next_resets else None
            rem_str = self.format_time_remaining(earliest_reset, now_dt=server_now_dt)
            reset_suffix = ""
            if earliest_reset and rem_str != "доступен сейчас! ⚡":
                reset_local = self._to_local_str(earliest_reset)
                if reset_local and reset_local != "N/A":
                    reset_suffix = f" (в {reset_local})"

            msg = (
                f"📊 <b>THUNDR СТАТУС</b>\n\n"
                f"👥 Всего аккаунтов: <b>{total}</b>\n"
                f"🎁 Доступно кейсов сейчас: <b>{ready}/{total}</b>\n"
                f"⏳ На перезарядке: <b>{total - ready}/{total}</b>\n"
                f"⏰ До ближайшего сброса: <b>{rem_str}</b>{reset_suffix}\n"
            )

            # Check jackpot status
            jackpot_users = []
            for a in accounts:
                st = getattr(a, "status", None) or getattr(a, "last_status", None)
                if isinstance(a, dict):
                    st = a.get("status") or a.get("last_status")
                streak = getattr(st, "streak", None) if st else None
                if streak is None and isinstance(st, dict):
                    streak = st.get("streak")
                if streak is None and isinstance(a, dict):
                    streak = a.get("streak")
                try:
                    s_val = int(streak) if streak is not None else 0
                except (ValueError, TypeError):
                    s_val = 0
                u_name = getattr(a, "username", "") if not isinstance(a, dict) else a.get("username", "")
                if s_val > 0 and s_val % 7 == 0:
                    jackpot_users.append(u_name)

            if jackpot_users:
                jp_str = ", ".join(f"<code>{html.escape(str(u))}</code>" for u in jackpot_users[:5])
                msg += f"\n🎰 <b>Сегодня день джекпота (7-й день):</b> {jp_str}\n"

            if ready_accs:
                names = ", ".join(f"<code>{html.escape(a.username)}</code>" for a in ready_accs[:10])
                msg += f"\n🎉 <b>Готовы к сбору:</b> {names}\n"
            msg += f"\n💡 <i>Отправьте /claim чтобы собрать готовые кейсы.</i>\n\n{self.get_execution_environment_label()}"
            return msg

        elif (
            base_cmd.startswith("/stats_coins")
            or base_cmd.startswith("/stats-coins")
            or base_cmd.startswith("/статс_монеты")
            or base_cmd.startswith("/статс-монеты")
            or base_cmd.startswith("/coins")
            or base_cmd.startswith("/coin")
            or base_cmd.startswith("/монеты")
            or base_cmd.startswith("/монета")
        ):
            self.set_stats_coins_requested(True)
            return (
                "⏳ <b>Запрос на подробную статистику по монетам всех аккаунтов принят.</b>\n"
                "Будет отправлена при следующем запуске раннера.\n\n"
                f"{self.get_execution_environment_label()}"
            )

        elif (
            base_cmd.startswith("/stats")
            or base_cmd.startswith("/stat")
            or base_cmd.startswith("/статс")
            or base_cmd.startswith("/стат")
            or base_cmd.startswith("/статистика")
        ):
            self.set_stats_requested(True)
            return (
                "⏳ <b>Запрос на подробную статистику по всем аккаунтам принят.</b>\n"
                "Будет отправлена при следующем запуске раннера.\n\n"
                f"{self.get_execution_environment_label()}"
            )

        elif base_cmd.startswith("/start") or base_cmd.startswith("/help"):
            return (
                f"⚡ <b>Thundr Daily Rewards Bot</b>\n\n"
                f"Доступные команды:\n"
                f"• /summary — Полная сводка по аккаунтам\n"
                f"• /stats — Доступные минуты буста по каждому аккаунту на следующий запуск\n"
                f"• /stats_coins — Точный баланс монет по каждому аккаунту на следующий запуск\n"
                f"• /goal — Прогресс достижения цели (50+ монет)\n"
                f"• /goal &lt;логин&gt; — Прогресс конкретного аккаунта\n"
                f"• /boost — Магазин бустов и готовые аккаунты\n"
                f"• /boost buy &lt;логин&gt; [тип] — Активировать буст\n"
                f"• /status — Статус кейсов и таймер кулдауна\n"
                f"• /claim — Моментальный сбор наград для всех аккаунтов\n"
                f"• /claim &lt;логин&gt; — Сбор наград для конкретного аккаунта\n"
                f"• /help — Список команд\n\n"
                f"{self.get_execution_environment_label()}"
            )
        return None


