#!/usr/bin/env python3
"""
Desktop Graphical User Interface (GUI) for Thundr Daily Rewards.
Built with CustomTkinter for a modern dark-theme dashboard.
Visualizes accounts, balances, streaks, live countdown timers, and automates claims.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

# Ensure UTF-8 output on Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import thundr.env_loader

import customtkinter as ctk
from tkinter import messagebox

from thundr import (
    AccountManager,
    AccountRecord,
    DailyCrateStateMachine,
    DailyCrateStatus,
    StorageManager,
    TelegramNotifier,
    ThundrApiClient,
    ThundrBrowserClient,
    ThundrClient,
)
from thundr.state_machine import CrateState

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class ThundrGUI(ctk.CTk):
    """Main Application Window for Thundr Rewards Automation."""

    def __init__(self) -> None:
        super().__init__()

        self.title("⚡ Thundr Daily Rewards Manager")
        self.geometry("1100x750")
        self.minsize(950, 650)

        # Timezone
        tz_name = os.getenv("APP_TIMEZONE", "Europe/Berlin")
        try:
            self.tz = ZoneInfo(tz_name)
        except Exception:
            self.tz = timezone.utc

        # Managers
        self.account_mgr = AccountManager()
        self.storage_mgr = StorageManager()
        self.telegram = TelegramNotifier()

        # Clients
        api_client = ThundrApiClient()
        browser_client = ThundrBrowserClient(
            headless=os.getenv("HEADLESS_BROWSER", "true").lower() == "true"
        )
        self.client = ThundrClient(api_client, browser_client)
        self.state_machine = DailyCrateStateMachine(self.client)

        # Background Daemon state
        self.daemon_active = False
        self.daemon_interval_sec = int(os.getenv("CHECK_INTERVAL_HOURS", "3")) * 3600
        self.last_daemon_run: Optional[float] = None
        self.daemon_thread: Optional[threading.Thread] = None

        # Build UI layout
        self._build_header()
        self._build_action_bar()
        self._build_main_content()
        self._build_status_bar()

        # Start periodic tick timers
        self._tick_timers()

        # Initial population of accounts
        self.refresh_accounts_view()
        self.log("🚀 Thundr Daily Rewards Manager запущен и готов к работе.")

    # -------------------------------------------------------------------------
    # UI Building Blocks
    # -------------------------------------------------------------------------

    def _build_header(self) -> None:
        header_frame = ctk.CTkFrame(self, corner_radius=10, fg_color="#181824")
        header_frame.pack(fill="x", padx=15, pady=(15, 8))

        # Title and logo
        title_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_box.pack(side="left", padx=15, pady=12)

        lbl_title = ctk.CTkLabel(
            title_box,
            text="⚡ THUNDR REWARDS",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#FFB300",
        )
        lbl_title.pack(anchor="w")

        lbl_subtitle = ctk.CTkLabel(
            title_box,
            text="Автоматизация ежедневных кейсов и учёт монет",
            font=ctk.CTkFont(size=12),
            text_color="#9E9E9E",
        )
        lbl_subtitle.pack(anchor="w")

        # Right-side indicators
        right_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        right_box.pack(side="right", padx=15, pady=10)

        # Telegram badge
        self.lbl_tg_badge = ctk.CTkLabel(
            right_box,
            text="🔔 Telegram: Подключен" if self.telegram.is_configured else "🔔 Telegram: Не настроен",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#1B5E20" if self.telegram.is_configured else "#5D4037",
            text_color="#E8F5E9" if self.telegram.is_configured else "#FFCCBC",
            corner_radius=6,
            padx=10,
            pady=4,
        )
        self.lbl_tg_badge.pack(side="left", padx=6)

        # Clock
        self.lbl_clock = ctk.CTkLabel(
            right_box,
            text="",
            font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
            text_color="#E0E0E0",
            fg_color="#263238",
            corner_radius=6,
            padx=10,
            pady=4,
        )
        self.lbl_clock.pack(side="left", padx=6)

    def _build_action_bar(self) -> None:
        action_frame = ctk.CTkFrame(self, corner_radius=10, fg_color="#1F202E")
        action_frame.pack(fill="x", padx=15, pady=6)

        btn_box = ctk.CTkFrame(action_frame, fg_color="transparent")
        btn_box.pack(side="left", padx=10, pady=8)

        # Claim All button
        self.btn_claim_all = ctk.CTkButton(
            btn_box,
            text="⚡ Собрать монеты",
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#FF8F00",
            hover_color="#FFA000",
            text_color="#121212",
            width=150,
            command=self._on_claim_all_clicked,
        )
        self.btn_claim_all.pack(side="left", padx=5)

        # Check All button
        self.btn_check_all = ctk.CTkButton(
            btn_box,
            text="🔄 Проверить все",
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#1976D2",
            hover_color="#2196F3",
            width=140,
            command=self._on_check_all_clicked,
        )
        self.btn_check_all.pack(side="left", padx=5)

        # Add Account button
        self.btn_add_acc = ctk.CTkButton(
            btn_box,
            text="➕ Добавить аккаунт",
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#388E3C",
            hover_color="#43A047",
            width=150,
            command=self._open_add_account_modal,
        )
        self.btn_add_acc.pack(side="left", padx=5)

        # Telegram Settings button
        self.btn_tg_settings = ctk.CTkButton(
            btn_box,
            text="⚙️ Telegram",
            font=ctk.CTkFont(size=13),
            fg_color="#455A64",
            hover_color="#546E7A",
            width=110,
            command=self._open_telegram_modal,
        )
        self.btn_tg_settings.pack(side="left", padx=5)

        # Auto-check Daemon Toggle
        daemon_box = ctk.CTkFrame(action_frame, fg_color="transparent")
        daemon_box.pack(side="right", padx=15, pady=8)

        self.daemon_switch = ctk.CTkSwitch(
            daemon_box,
            text="Авто-проверка раз в 3 часа",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._on_daemon_toggle,
        )
        self.daemon_switch.pack(side="left", padx=5)

    def _build_main_content(self) -> None:
        content_frame = ctk.CTkFrame(self, fg_color="transparent")
        content_frame.pack(fill="both", expand=True, padx=15, pady=6)

        # Split into accounts list (top/left) and logs (bottom)
        # Accounts scrollable container
        self.accounts_container = ctk.CTkScrollableFrame(
            content_frame,
            label_text="👥 Список аккаунтов и статусы кейсов",
            label_font=ctk.CTkFont(size=14, weight="bold"),
            corner_radius=10,
            fg_color="#1A1A24",
        )
        self.accounts_container.pack(fill="both", expand=True, pady=(0, 8))

        # Activity log frame
        log_frame = ctk.CTkFrame(content_frame, height=160, corner_radius=10, fg_color="#181822")
        log_frame.pack(fill="x", pady=0)
        log_frame.pack_propagate(False)

        lbl_log = ctk.CTkLabel(
            log_frame,
            text="📋 Журнал активности (Live Activity Log)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#B0BEC5",
        )
        lbl_log.pack(anchor="w", padx=12, pady=(6, 2))

        self.log_textbox = ctk.CTkTextbox(
            log_frame,
            font=ctk.CTkFont(family="Consolas", size=12),
            text_color="#ECEFF1",
            fg_color="#121218",
            corner_radius=6,
        )
        self.log_textbox.pack(fill="both", expand=True, padx=8, pady=(0, 6))

    def _build_status_bar(self) -> None:
        status_frame = ctk.CTkFrame(self, height=28, corner_radius=0, fg_color="#121218")
        status_frame.pack(fill="x", side="bottom")

        self.lbl_status = ctk.CTkLabel(
            status_frame,
            text="Готов к работе.",
            font=ctk.CTkFont(size=11),
            text_color="#90A4AE",
        )
        self.lbl_status.pack(side="left", padx=15)

        self.lbl_total_coins = ctk.CTkLabel(
            status_frame,
            text="💰 Суммарный баланс: 0 coins",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#FFD54F",
        )
        self.lbl_total_coins.pack(side="right", padx=15)

    # -------------------------------------------------------------------------
    # Periodic Timers & Live Updates
    # -------------------------------------------------------------------------

    def _tick_timers(self) -> None:
        now_dt = datetime.now(self.tz)
        # Update clock
        self.lbl_clock.configure(text=now_dt.strftime("%d.%m.%Y %H:%M:%S %Z"))

        # Check background daemon
        if self.daemon_active:
            now_ts = time.time()
            if self.last_daemon_run is None or (now_ts - self.last_daemon_run >= self.daemon_interval_sec):
                self.last_daemon_run = now_ts
                self.log("🕒 Сработал авто-таймер (каждые 3 часа). Запуск проверки аккаунтов...")
                threading.Thread(target=self._execute_batch, args=(False,), daemon=True).start()

        # Update remaining cooldown timers for account cards
        self._update_account_timers()

        # Schedule next tick in 1 second
        self.after(1000, self._tick_timers)

    def _update_account_timers(self) -> None:
        if not hasattr(self, "account_cards"):
            return

        now_utc = datetime.now(timezone.utc)
        for username, card in self.account_cards.items():
            next_iso = card.get("next_reset_at")
            timer_lbl = card.get("timer_label")
            status_badge = card.get("status_badge")

            if not timer_lbl:
                continue

            if not next_iso:
                timer_lbl.configure(text="Таймер: неизвестно")
                continue

            try:
                dt = datetime.fromisoformat(next_iso.replace("Z", "+00:00"))
                diff = dt - now_utc
                total_sec = int(diff.total_seconds())

                if total_sec <= 0:
                    timer_lbl.configure(text="⚡ ГОТОВ К СБОРУ!", text_color="#00E676")
                    if status_badge:
                        status_badge.configure(text="ГОТОВ", fg_color="#1B5E20", text_color="#E8F5E9")
                else:
                    hours = total_sec // 3600
                    minutes = (total_sec % 3600) // 60
                    seconds = total_sec % 60
                    timer_lbl.configure(
                        text=f"⏳ {hours:02d}:{minutes:02d}:{seconds:02d}",
                        text_color="#FFD54F",
                    )
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # Accounts View Renderer
    # -------------------------------------------------------------------------

    def refresh_accounts_view(self) -> None:
        """Renders list of accounts as modern dark cards."""
        for widget in self.accounts_container.winfo_children():
            widget.destroy()

        self.account_cards = {}
        accounts = self.account_mgr.list_accounts()

        if not accounts:
            empty_frame = ctk.CTkFrame(self.accounts_container, fg_color="transparent")
            empty_frame.pack(fill="both", expand=True, pady=40)
            ctk.CTkLabel(
                empty_frame,
                text="Пока нет добавленных аккаунтов.\nНажмите '+ Добавить аккаунт' выше, чтобы зарегистрировать аккаунт.",
                font=ctk.CTkFont(size=14),
                text_color="#78909C",
            ).pack()
            self.lbl_total_coins.configure(text="💰 Суммарный баланс: 0 coins")
            return

        total_coins = 0

        for acc in accounts:
            card = ctk.CTkFrame(
                self.accounts_container,
                corner_radius=10,
                fg_color="#222332",
                border_width=1,
                border_color="#2C2D42",
            )
            card.pack(fill="x", padx=6, pady=5)

            # Left side: Username + Session status + Badge
            left_col = ctk.CTkFrame(card, fg_color="transparent")
            left_col.pack(side="left", padx=15, pady=10)

            user_row = ctk.CTkFrame(left_col, fg_color="transparent")
            user_row.pack(anchor="w")

            lbl_user = ctk.CTkLabel(
                user_row,
                text=acc.username,
                font=ctk.CTkFont(size=15, weight="bold"),
                text_color="#FFFFFF",
            )
            lbl_user.pack(side="left")

            # Status Badge
            is_avail = acc.last_status and acc.last_status.available
            badge_text = "ГОТОВ" if is_avail else "ОЖИДАНИЕ"
            badge_color = "#1B5E20" if is_avail else "#424242"
            badge_text_color = "#C8E6C9" if is_avail else "#B0BEC5"

            lbl_badge = ctk.CTkLabel(
                user_row,
                text=badge_text,
                font=ctk.CTkFont(size=11, weight="bold"),
                fg_color=badge_color,
                text_color=badge_text_color,
                corner_radius=5,
                padx=8,
                pady=2,
            )
            lbl_badge.pack(side="left", padx=10)

            sess_info = "🔑 Сессия: Активна" if acc.has_session else "⚠️ Сессия: Требуется вход"
            sess_color = "#81C784" if acc.has_session else "#FFB74D"
            lbl_sess = ctk.CTkLabel(
                left_col,
                text=sess_info,
                font=ctk.CTkFont(size=11),
                text_color=sess_color,
            )
            lbl_sess.pack(anchor="w")

            # Center stats: Balance, Streak, Last Reward, Countdown
            stats_col = ctk.CTkFrame(card, fg_color="transparent")
            stats_col.pack(side="left", expand=True, padx=10, pady=10)

            bal = acc.last_status.coin_balance if acc.last_status else 0
            streak = acc.last_status.streak if acc.last_status else 0
            total_coins += bal

            # Retrieve last reward
            last_rew = acc.last_reward_coins
            if last_rew is None:
                last_rew = self.storage_mgr.get_last_reward_for_user(acc.username)
            rew_str = f"+{last_rew} монет" if last_rew else "нет данных"

            stats_text = f"💰 Баланс: {bal} coins   |   🔥 Серия: {streak} дн.   |   🎁 В прошлый раз: {rew_str}"
            lbl_stats = ctk.CTkLabel(
                stats_col,
                text=stats_text,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color="#ECEFF1",
            )
            lbl_stats.pack(anchor="w")

            # Countdown Timer label
            lbl_timer = ctk.CTkLabel(
                stats_col,
                text="⏳ Расчёт таймера...",
                font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
                text_color="#FFD54F",
            )
            lbl_timer.pack(anchor="w", pady=(3, 0))

            # Right actions: Claim, Check, Remove
            actions_col = ctk.CTkFrame(card, fg_color="transparent")
            actions_col.pack(side="right", padx=15, pady=10)

            btn_claim = ctk.CTkButton(
                actions_col,
                text="⚡ Собрать",
                font=ctk.CTkFont(size=11, weight="bold"),
                fg_color="#FF8F00",
                hover_color="#FFA000",
                text_color="#121212",
                width=80,
                height=28,
                command=lambda u=acc.username: self._run_single_account(u, dry_run=False),
            )
            btn_claim.pack(side="left", padx=4)

            btn_check = ctk.CTkButton(
                actions_col,
                text="🔄 Проверить",
                font=ctk.CTkFont(size=11),
                fg_color="#1976D2",
                hover_color="#2196F3",
                width=85,
                height=28,
                command=lambda u=acc.username: self._check_single_account(u),
            )
            btn_check.pack(side="left", padx=4)

            btn_del = ctk.CTkButton(
                actions_col,
                text="🗑️",
                font=ctk.CTkFont(size=12),
                fg_color="#C62828",
                hover_color="#D32F2F",
                width=32,
                height=28,
                command=lambda u=acc.username: self._delete_account(u),
            )
            btn_del.pack(side="left", padx=4)

            # Store references for live timer update
            self.account_cards[acc.username] = {
                "next_reset_at": acc.last_status.next_reset_at if acc.last_status else None,
                "timer_label": lbl_timer,
                "status_badge": lbl_badge,
            }

        self.lbl_total_coins.configure(text=f"💰 Суммарный баланс: {total_coins} coins")

    # -------------------------------------------------------------------------
    # Core Operations
    # -------------------------------------------------------------------------

    def log(self, message: str) -> None:
        """Appends timestamped message to the live activity log."""
        timestamp = datetime.now(self.tz).strftime("%H:%M:%S")
        formatted = f"[{timestamp}] {message}\n"
        self.log_textbox.insert("end", formatted)
        self.log_textbox.see("end")

    def _set_controls_state(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.btn_claim_all.configure(state=state)
        self.btn_check_all.configure(state=state)
        self.btn_add_acc.configure(state=state)

    def _on_claim_all_clicked(self) -> None:
        self.log("⚡ Запуск сбора наград для всех аккаунтов...")
        threading.Thread(target=self._execute_batch, args=(False,), daemon=True).start()

    def _on_check_all_clicked(self) -> None:
        self.log("🔄 Проверка статусов всех аккаунтов...")
        threading.Thread(target=self._execute_batch_check, daemon=True).start()

    def _execute_batch(self, dry_run: bool = False) -> None:
        self._set_controls_state(False)
        self.lbl_status.configure(text="Выполняется сбор наград...")

        accounts = self.account_mgr.list_accounts(enabled_only=True)
        results = []

        for acc in accounts:
            self.log(f"▶ Обработка аккаунта: {acc.username}")
            if acc.last_reward_coins is None:
                acc.last_reward_coins = self.storage_mgr.get_last_reward_for_user(acc.username)

            ctx = self.state_machine.run(acc, dry_run=dry_run)
            results.append(ctx)

            if acc.session:
                self.account_mgr.update_session(acc.username, acc.session)
            if ctx.status:
                self.account_mgr.update_status(
                    acc.username,
                    ctx.status,
                    claimed=(ctx.current_state == CrateState.CLAIMED),
                    reward_coins=ctx.coins_earned if ctx.coins_earned > 0 else None,
                )

            self.storage_mgr.record_run(ctx)

            if ctx.current_state == CrateState.CLAIMED:
                self.log(f"✅ [{acc.username}] Награда получена! +{ctx.coins_earned} монет (Баланс: {ctx.final_balance})")
            elif ctx.current_state == CrateState.COOLDOWN:
                rem = TelegramNotifier.format_time_remaining(ctx.status.next_reset_at if ctx.status else None)
                self.log(f"⏳ [{acc.username}] На перезарядке. До кейса: {rem} (Баланс: {ctx.final_balance or ctx.initial_balance})")
            else:
                self.log(f"⚠️ [{acc.username}] Статус: {ctx.current_state.value} ({ctx.error_message or 'OK'})")

            # Single Telegram notification
            if self.telegram.is_configured:
                self.telegram.notify_single_run(ctx)

        # Batch summary to Telegram
        if self.telegram.is_configured and len(results) > 1:
            self.telegram.notify_summary(results)

        self.after(0, self.refresh_accounts_view)
        self.after(0, lambda: self._set_controls_state(True))
        self.lbl_status.configure(text="Сбор завершён.")
        self.log("✨ Сбор наград для всех аккаунтов завершён.")

    def _execute_batch_check(self) -> None:
        self._set_controls_state(False)
        self.lbl_status.configure(text="Проверка балансов и таймеров...")

        accounts = self.account_mgr.list_accounts(enabled_only=True)
        for acc in accounts:
            try:
                status = self.client.get_crate_status(acc)
                self.account_mgr.update_status(acc.username, status)
                rem = TelegramNotifier.format_time_remaining(status.next_reset_at)
                self.log(f"🔍 [{acc.username}] Баланс: {status.coin_balance} coins | До кейса: {rem} | Серия: {status.streak} дн.")
            except Exception as e:
                self.log(f"❌ [{acc.username}] Ошибка проверки: {e}")

        self.after(0, self.refresh_accounts_view)
        self.after(0, lambda: self._set_controls_state(True))
        self.lbl_status.configure(text="Проверка завершена.")

    def _run_single_account(self, username: str, dry_run: bool = False) -> None:
        def _worker():
            acc = self.account_mgr.get_account(username)
            if not acc:
                return
            self.log(f"▶ Запуск сбора для: {username}")
            if acc.last_reward_coins is None:
                acc.last_reward_coins = self.storage_mgr.get_last_reward_for_user(acc.username)

            ctx = self.state_machine.run(acc, dry_run=dry_run)

            if acc.session:
                self.account_mgr.update_session(acc.username, acc.session)
            if ctx.status:
                self.account_mgr.update_status(
                    acc.username,
                    ctx.status,
                    claimed=(ctx.current_state == CrateState.CLAIMED),
                    reward_coins=ctx.coins_earned if ctx.coins_earned > 0 else None,
                )

            self.storage_mgr.record_run(ctx)
            if self.telegram.is_configured:
                self.telegram.notify_single_run(ctx)

            rem = TelegramNotifier.format_time_remaining(ctx.status.next_reset_at if ctx.status else None)
            if ctx.current_state == CrateState.CLAIMED:
                self.log(f"🎉 [{username}] Успешно получено: +{ctx.coins_earned} монет! До след.: {rem}")
            else:
                self.log(f"ℹ️ [{username}] {ctx.current_state.value}. До кейса: {rem}")

            self.after(0, self.refresh_accounts_view)

        threading.Thread(target=_worker, daemon=True).start()

    def _check_single_account(self, username: str) -> None:
        def _worker():
            acc = self.account_mgr.get_account(username)
            if not acc:
                return
            try:
                status = self.client.get_crate_status(acc)
                self.account_mgr.update_status(acc.username, status)
                rem = TelegramNotifier.format_time_remaining(status.next_reset_at)
                self.log(f"🔍 [{username}] Баланс: {status.coin_balance} coins | До кейса: {rem} | Серия: {status.streak} дн.")
            except Exception as e:
                self.log(f"❌ [{username}] Ошибка: {e}")
            self.after(0, self.refresh_accounts_view)

        threading.Thread(target=_worker, daemon=True).start()

    def _delete_account(self, username: str) -> None:
        if messagebox.askyesno("Удаление", f"Вы уверены, что хотите удалить аккаунт '{username}'?"):
            self.account_mgr.remove_account(username)
            self.refresh_accounts_view()
            self.log(f"🗑️ Аккаунт '{username}' удалён.")

    def _on_daemon_toggle(self) -> None:
        self.daemon_active = bool(self.daemon_switch.get())
        if self.daemon_active:
            self.log("🕒 Фоновая авто-проверка включена (каждые 3 часа).")
            self.last_daemon_run = time.time()  # Start interval countdown
        else:
            self.log("⏸️ Фоновая авто-проверка выключена.")

    # -------------------------------------------------------------------------
    # Modals: Add Account & Telegram Settings
    # -------------------------------------------------------------------------

    def _open_add_account_modal(self) -> None:
        modal = ctk.CTkToplevel(self)
        modal.title("Добавить новый аккаунт Thundr")
        modal.geometry("450x380")
        modal.resizable(False, False)
        modal.grab_set()

        ctk.CTkLabel(
            modal,
            text="➕ Добавление аккаунта Thundr",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(pady=(20, 15))

        entry_user = ctk.CTkEntry(modal, placeholder_text="Username / Логин", width=350)
        entry_user.pack(pady=8)

        entry_pass = ctk.CTkEntry(modal, placeholder_text="Password / Пароль", show="*", width=350)
        entry_pass.pack(pady=8)

        chk_auth = ctk.CTkCheckBox(modal, text="Сразу проверить вход и получить статус кейса")
        chk_auth.select()
        chk_auth.pack(pady=10)

        lbl_status = ctk.CTkLabel(modal, text="", font=ctk.CTkFont(size=12), text_color="#FFB300")
        lbl_status.pack(pady=5)

        def _save():
            user = entry_user.get().strip()
            pw = entry_pass.get().strip()
            if not user or not pw:
                lbl_status.configure(text="Заполните имя пользователя и пароль!")
                return

            lbl_status.configure(text="Проверка авторизации...")
            btn_submit.configure(state="disabled")

            def _worker():
                try:
                    # Add account
                    rec = self.account_mgr.add_account(username=user, password=pw)
                    if chk_auth.get():
                        status = self.client.get_crate_status(rec)
                        self.account_mgr.update_status(user, status)
                    self.after(0, lambda: self.log(f"✔ Аккаунт '{user}' успешно добавлен!"))
                    self.after(0, self.refresh_accounts_view)
                    self.after(0, modal.destroy)
                except Exception as err:
                    self.after(0, lambda: lbl_status.configure(text=f"Ошибка: {str(err)[:50]}"))
                    self.after(0, lambda: btn_submit.configure(state="normal"))

            threading.Thread(target=_worker, daemon=True).start()

        btn_submit = ctk.CTkButton(
            modal,
            text="Сохранить и подключить",
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#388E3C",
            hover_color="#43A047",
            width=250,
            command=_save,
        )
        btn_submit.pack(pady=(15, 10))

    def _open_telegram_modal(self) -> None:
        modal = ctk.CTkToplevel(self)
        modal.title("Настройки Telegram")
        modal.geometry("480x360")
        modal.resizable(False, False)
        modal.grab_set()

        ctk.CTkLabel(
            modal,
            text="⚙️ Настройки Telegram Бота",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(pady=(20, 10))

        ctk.CTkLabel(
            modal,
            text="Вы можете легко переключить бота или канал в любой момент:",
            font=ctk.CTkFont(size=11),
            text_color="#9E9E9E",
        ).pack(pady=(0, 10))

        ctk.CTkLabel(modal, text="Bot Token:", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=45)
        entry_token = ctk.CTkEntry(modal, width=390)
        entry_token.insert(0, os.getenv("TELEGRAM_BOT_TOKEN", ""))
        entry_token.pack(pady=(2, 10))

        ctk.CTkLabel(modal, text="Chat ID:", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=45)
        entry_chat = ctk.CTkEntry(modal, width=390)
        entry_chat.insert(0, os.getenv("TELEGRAM_CHAT_ID", ""))
        entry_chat.pack(pady=(2, 15))

        lbl_tg_res = ctk.CTkLabel(modal, text="", font=ctk.CTkFont(size=12))
        lbl_tg_res.pack(pady=4)

        def _test_and_save():
            t = entry_token.get().strip()
            c = entry_chat.get().strip()
            if not t or not c:
                lbl_tg_res.configure(text="Введите Bot Token и Chat ID!", text_color="#E53935")
                return

            lbl_tg_res.configure(text="Отправка тестового сообщения...", text_color="#FFB300")

            def _test_worker():
                test_notifier = TelegramNotifier(bot_token=t, chat_id=c)
                ok = test_notifier.send_raw_message(
                    "⚡ <b>Thundr Daily Manager</b>: Тестовое уведомление успешно доставлено!"
                )
                if ok:
                    # Save to .env
                    env_path = PROJECT_ROOT / ".env"
                    lines = []
                    if env_path.exists():
                        lines = env_path.read_text(encoding="utf-8").splitlines()
                    # Filter existing
                    lines = [l for l in lines if not l.startswith("TELEGRAM_BOT_TOKEN=") and not l.startswith("TELEGRAM_CHAT_ID=")]
                    lines.append(f"TELEGRAM_BOT_TOKEN={t}")
                    lines.append(f"TELEGRAM_CHAT_ID={c}")
                    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    os.environ["TELEGRAM_BOT_TOKEN"] = t
                    os.environ["TELEGRAM_CHAT_ID"] = c
                    self.telegram = TelegramNotifier()

                    self.after(0, lambda: lbl_tg_res.configure(text="✔ Сообщение доставлено и сохранено!", text_color="#43A047"))
                    self.after(0, lambda: self.lbl_tg_badge.configure(
                        text="🔔 Telegram: Подключен", fg_color="#1B5E20", text_color="#E8F5E9"
                    ))
                    self.after(1500, modal.destroy)
                else:
                    self.after(0, lambda: lbl_tg_res.configure(text="❌ Ошибка доставки! Проверьте токен и ID.", text_color="#E53935"))

            threading.Thread(target=_test_worker, daemon=True).start()

        btn_save_tg = ctk.CTkButton(
            modal,
            text="Проверить и сохранить",
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#1976D2",
            hover_color="#2196F3",
            width=250,
            command=_test_and_save,
        )
        btn_save_tg.pack(pady=10)


def main():
    app = ThundrGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
