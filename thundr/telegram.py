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

    def notify_single_run(self, ctx: StateMachineContext, force: bool = False) -> bool:
        """Send notification for a single account execution."""
        if not force and not self.should_notify_single(ctx):
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

        # Date and claim status for today
        today_date_str = datetime.now(self.tz).strftime("%Y-%m-%d")
        today_display = datetime.now(self.tz).strftime("%d.%m")

        claimed_today_count = 0
        for c in contexts:
            if c.current_state == CrateState.CLAIMED:
                claimed_today_count += 1
            elif c.status and c.status.last_claimed_on == today_date_str:
                claimed_today_count += 1
            elif c.current_state == CrateState.COOLDOWN and c.status and not c.status.available:
                claimed_today_count += 1

        if claimed_today_count == total_accounts:
            date_claim_line = f"📅 <b>Награда за {today_display}:</b> ✅ Забрана со всех ({total_accounts}/{total_accounts})"
        elif claimed_today_count == 0:
            date_claim_line = f"📅 <b>Награда за {today_display}:</b> ⏳ Ещё не забрана (0/{total_accounts} — доступна!)"
        else:
            date_claim_line = f"📅 <b>Награда за {today_display}:</b> ⚠️ Забрана частично ({claimed_today_count}/{total_accounts})"

        # Accounts that dropped 50+ coins from the crate
        dropped_50_plus = []
        for c in contexts:
            earned = c.coins_earned if c.coins_earned > 0 else (c.last_reward_coins or 0)
            if earned >= 50:
                dropped_50_plus.append((c.username, earned))

        if dropped_50_plus:
            dropped_details = ", ".join(f"<code>{html.escape(u)}</code> (+{amt})" for u, amt in dropped_50_plus)
            drop_50_line = f"🎁 <b>Сегодня выпало 50+:</b> {len(dropped_50_plus)} ({dropped_details})"
        else:
            drop_50_line = "🎁 <b>Сегодня выпало 50+:</b> 0"


        # Top account by balance
        best_ctx = max(contexts, key=lambda c: (c.final_balance or c.initial_balance or 0)) if contexts else None
        best_user = best_ctx.username if best_ctx else "N/A"
        best_bal = (best_ctx.final_balance or best_ctx.initial_balance or 0) if best_ctx else 0

        # Next reset timing
        next_resets = [c.status.next_reset_at for c in contexts if c.status and c.status.next_reset_at]
        next_reset_ts = min(next_resets) if next_resets else None
        rem_str = self.format_time_remaining(next_reset_ts)

        # Error line
        if failed_count == 0:
            error_line = "✔ <b>Ошибки:</b> 0 (всё чётко)"
        else:
            error_line = f"❌ <b>Ошибки:</b> {failed_count} акк."

        # Assemble compact message
        msg_parts = [
            f"📊 <b>THUNDR REPORT — СВОДКА</b>",
            f"<i>{now_str}</i>\n",
            f"👥 Всего аккаунтов: <b>{total_accounts}</b>",
            date_claim_line,
            error_line,
            f"━━━━━━━━━━━━━━━━━━━",
            f"💰 <b>Общий баланс:</b> {total_balance} монет" + (f" (+{total_coins_earned})" if total_coins_earned > 0 else ""),
            f"📊 <b>В среднем на 1 аккаунт:</b> {avg_balance:.1f} монет",
            drop_50_line,
            f"🎯 <b>Цель (50+ баланс):</b> {sum(1 for b in balances if b >= 50)}/{total_accounts} акк." + (f" | ⚡ Буст (100+): {sum(1 for b in balances if b >= 100)}" if any(b >= 100 for b in balances) else ""),
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

        if base_cmd.startswith("/goal"):
            if not accounts:
                return "🎯 <b>Thundr Goal</b>\n\nНет данных об аккаунтах для расчета цели."

            # Check if specific account or custom target threshold was passed
            arg = parts[1].strip() if len(parts) > 1 else None
            custom_target: Optional[int] = None
            specific_acc = None

            if arg:
                if arg.isdigit():
                    custom_target = int(arg)
                else:
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
                return (
                    f"🎯 <b>THUNDR GOAL — {html.escape(specific_acc.username)}</b>\n\n"
                    f"{res_line}\n"
                    f"🔥 Стрик: <b>{getattr(getattr(specific_acc, 'status', None), 'streak', 0)}</b> дн."
                )

            # Pool summary
            target = custom_target or 50
            balances = [getattr(a, "balance", 0) or 0 for a in accounts]
            total_bal = sum(balances)
            count_target = sum(1 for b in balances if b >= target)
            count_100 = sum(1 for b in balances if b >= 100)
            total = len(accounts)
            pct = (count_target / total * 100) if total > 0 else 0

            # Find reached accounts
            reached_names = [
                f"{a.username} ({getattr(a, 'balance', 0)})"
                for a in accounts
                if (getattr(a, "balance", 0) or 0) >= target
            ]
            reached_str = ", ".join(f"<code>{html.escape(n)}</code>" for n in reached_names[:10])
            if len(reached_names) > 10:
                reached_str += f" и ещё {len(reached_names) - 10}..."

            msg_parts = [
                f"🎯 <b>THUNDR GOAL REPORT — ЦЕЛЬ {target} МОНЕТ</b>\n",
                f"👥 Всего аккаунтов: <b>{total}</b>",
                f"💰 Общий баланс: <b>{total_bal}</b> монет (в среднем {total_bal / total:.1f}/акк.)",
                f"✅ Достигли цели ({target}+): <b>{count_target}/{total}</b> ({pct:.1f}%)",
                f"⚡ Достигли 100+ (Mini Boost): <b>{count_100}/{total}</b>",
            ]
            if reached_names:
                msg_parts.append(f"\n🏆 <b>Достигли цели:</b>\n{reached_str}")

            return "\n".join(msg_parts)

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
                    return "⚠️ Укажите имя аккаунта: <code>/boost buy &lt;логин&gt; [тип]</code>"

                if boost_type not in cost_map:
                    return f"❌ Неверный тип буста '{html.escape(boost_type)}'. Доступны: mini, small, normal, super."

                matched_acc = None
                if accounts:
                    for a in accounts:
                        if getattr(a, "username", "").lower() == user_arg.lower():
                            matched_acc = a
                            break

                if not matched_acc:
                    return f"❌ Аккаунт '<code>{html.escape(user_arg)}</code>' не найден."

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
                        f"💡 Для покупки отправьте: <code>/boost buy {matched_acc.username} {boost_type}</code>"
                    )

                # Attempt purchase
                if bal < cost:
                    return (
                        f"❌ Недостаточно монет у <code>{html.escape(matched_acc.username)}</code>: "
                        f"баланс <b>{bal}</b>, требуется <b>{cost}</b> для {boost_type}."
                    )

                if client is None:
                    try:
                        from thundr.client import ThundrApiClient, ThundrBrowserClient, ThundrClient
                        client = ThundrClient(
                            ThundrApiClient(),
                            ThundrBrowserClient(headless=True),
                        )
                    except Exception as e:
                        return f"❌ Ошибка инициализации клиента: {e}"

                try:
                    res = client.spend_boost(matched_acc, boost=boost_type)
                    return (
                        f"✅ <b>Буст {boost_type.upper()} успешно активирован!</b>\n\n"
                        f"👤 Аккаунт: <code>{html.escape(matched_acc.username)}</code>\n"
                        f"💰 Списано: <b>{cost}</b> монет\n"
                        f"🎉 Результат: {html.escape(str(res.get('message', 'Успешно активирован!')))}"
                    )
                except Exception as e:
                    return f"❌ Ошибка при покупке буста для <code>{html.escape(matched_acc.username)}</code>: {html.escape(str(e))}"

            # General boost store display
            eligible_accs = [
                f"{a.username} ({getattr(a, 'balance', 0)})"
                for a in (accounts or [])
                if (getattr(a, "balance", 0) or 0) >= 100
            ]
            eligible_str = ", ".join(f"<code>{html.escape(n)}</code>" for n in eligible_accs) if eligible_accs else "нет"

            return (
                f"⚡ <b>THUNDR BOOST STORE — МАГАЗИН БУСТОВ</b>\n\n"
                f"• <b>Mini Boost:</b> 100 монет (1 час)\n"
                f"• <b>Small Boost:</b> 450 монет (10 часов)\n"
                f"• <b>Normal Boost:</b> 1000 монет (30 часов)\n"
                f"• <b>Super Boost:</b> 2100 монет (60 часов)\n\n"
                f"💎 Аккаунтов с балансом >= 100 монет (Mini): <b>{ready_mini}</b>\n"
                f"👤 Готовы к бусту: {eligible_str}\n\n"
                f"💡 <i>Для покупки отправьте:</i> <code>/boost buy &lt;логин&gt; [mini]</code>"
            )

        elif base_cmd.startswith("/status") or base_cmd.startswith("/check"):
            if not accounts:
                return "🔍 Нет данных об аккаунтах."
            ready_accs = [a for a in accounts if getattr(getattr(a, "status", None), "available", False)]
            ready = len(ready_accs)
            total = len(accounts)

            # Earliest reset
            next_resets = [
                a.status.next_reset_at
                for a in accounts
                if getattr(a, "status", None) and a.status.next_reset_at
            ]
            rem_str = self.format_time_remaining(min(next_resets) if next_resets else None)

            msg = (
                f"📊 <b>THUNDR СТАТУС</b>\n\n"
                f"👥 Всего аккаунтов: <b>{total}</b>\n"
                f"🎁 Доступно кейсов сейчас: <b>{ready}/{total}</b>\n"
                f"⏳ На перезарядке: <b>{total - ready}/{total}</b>\n"
                f"⏰ До ближайшего сброса: <b>{rem_str}</b>\n"
            )
            if ready_accs:
                names = ", ".join(f"<code>{html.escape(a.username)}</code>" for a in ready_accs[:10])
                msg += f"\n🎉 <b>Готовы к сбору:</b> {names}\n"
            msg += "\n💡 <i>Отправьте /claim чтобы собрать готовые кейсы.</i>"
            return msg

        elif base_cmd.startswith("/start") or base_cmd.startswith("/help"):
            return (
                f"⚡ <b>Thundr Daily Rewards Bot</b>\n\n"
                f"Доступные команды:\n"
                f"• /goal — Прогресс достижения цели (50+ монет)\n"
                f"• /goal &lt;логин&gt; — Прогресс конкретного аккаунта\n"
                f"• /boost — Магазин бустов и готовые аккаунты\n"
                f"• /boost buy &lt;логин&gt; [тип] — Активировать буст\n"
                f"• /status — Статус кейсов и таймер кулдауна\n"
                f"• /claim — Моментальный сбор наград для всех аккаунтов\n"
                f"• /claim &lt;логин&gt; — Сбор наград для конкретного аккаунта\n"
                f"• /help — Список команд"
            )
        return None


