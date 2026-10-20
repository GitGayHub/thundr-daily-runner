#!/usr/bin/env python3
"""
Command Line Interface (CLI) for Thundr Daily Rewards Automation.
Provides interactive account management, automated reward claiming, diagnostics, and export.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Ensure UTF-8 output on Windows consoles
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


import thundr.env_loader

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from thundr import (
    AccountManager,
    DailyCrateStateMachine,
    StorageManager,
    TelegramNotifier,
    ThundrApiClient,
    ThundrBrowserClient,
    ThundrClient,
)
from thundr.account import mask_secret
from thundr.client import ThundrAuthError, ThundrNetworkError
from thundr.state_machine import CrateState, StateMachineContext

# Rich console & logger
console = Console()
logger = logging.getLogger("thundr.cli")


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose debug logging.")
def cli(verbose: bool) -> None:
    """⚡ Thundr Daily Rewards Automation CLI ⚡"""
    configure_logging(verbose)


def _process_pending_telegram_updates(
    notifier: TelegramNotifier,
    account_mgr: AccountManager,
    client: Optional[ThundrClient] = None,
) -> None:
    """Check and reply to any pending Telegram slash commands."""
    try:
        offset = notifier.get_last_update_id()
        if offset is not None:
            offset += 1
        updates = notifier.get_updates(offset=offset, timeout=2)
        if not updates:
            return
        accounts = account_mgr.list_accounts(enabled_only=True)
        for upd in updates:
            upd_id = upd.get("update_id", 0)
            notifier.save_last_update_id(upd_id)
            msg = upd.get("message") or {}
            chat = msg.get("chat") or {}
            if str(chat.get("id")) != str(notifier.chat_id):
                continue
            text = (msg.get("text") or "").strip()
            if not text.startswith("/"):
                continue
            logger.info(f"Processing Telegram command from update {upd_id}: {text}")
            reply = notifier.handle_command(text, accounts=accounts, client=client)
            if reply:
                notifier.send_raw_message(reply)
                account_mgr.save()
    except Exception as e:
        logger.debug(f"Error checking pending Telegram updates: {e}")


def _execute_rewards(
    account_name: Optional[str] = None,
    dry_run: bool = False,
    no_telegram: bool = False,
    force_notify: bool = False,
) -> None:
    """Internal helper to execute reward claiming for accounts."""
    console.print("[bold cyan]🚀 Starting Thundr Daily Rewards Runner...[/bold cyan]")

    account_mgr = AccountManager()
    storage_mgr = StorageManager()
    telegram_notifier = TelegramNotifier() if not no_telegram else None

    accounts = account_mgr.list_accounts(enabled_only=True)
    if account_name:
        key = account_name.strip().lower()
        accounts = [a for a in accounts if a.username.lower() == key]
        if not accounts:
            console.print(f"[bold red]❌ Account '{account_name}' not found or is disabled.[/bold red]")
            sys.exit(1)

    if not accounts:
        console.print("[yellow]⚠️ No accounts configured! Use `python cli.py add-account` to add one.[/yellow]")
        return

    console.print(f"Found [bold green]{len(accounts)}[/bold green] account(s) to process.")

    api_client = ThundrApiClient()
    browser_client = ThundrBrowserClient(
        headless=os.getenv("HEADLESS_BROWSER", "true").lower() == "true"
    )
    client = ThundrClient(api_client, browser_client)
    state_machine = DailyCrateStateMachine(client)

    # Process any pending telegram commands if Telegram is configured
    if telegram_notifier and telegram_notifier.is_configured:
        _process_pending_telegram_updates(telegram_notifier, account_mgr, client)

    results = []
    try:
        for acc in accounts:
            console.print(f"\n[bold yellow]▶ Processing account: {acc.username}[/bold yellow]")
            try:
                if acc.last_reward_coins is None:
                    acc.last_reward_coins = storage_mgr.get_last_reward_for_user(acc.username)

                ctx = state_machine.run(acc, dry_run=dry_run)
                results.append(ctx)

                # Update account store with latest session & status
                if acc.session:
                    account_mgr.update_session(acc.username, acc.session)
                if ctx.status:
                    account_mgr.update_status(
                        acc.username,
                        ctx.status,
                        claimed=(ctx.current_state == CrateState.CLAIMED),
                        reward_coins=ctx.coins_earned if ctx.coins_earned > 0 else None,
                    )

                # Record in history
                storage_mgr.record_run(ctx)

                # Send individual Telegram alert if configured
                if telegram_notifier and (len(accounts) == 1 or telegram_notifier.mode == "all"):
                    telegram_notifier.notify_single_run(ctx, force=(len(accounts) == 1))
            except Exception as acc_err:
                logger.exception(f"Unexpected error processing account '{acc.username}': {acc_err}")
                err_ctx = StateMachineContext(
                    username=acc.username,
                    current_state=CrateState.FAILED,
                    error_message=str(acc_err),
                )
                results.append(err_ctx)
                storage_mgr.record_run(err_ctx)
                if telegram_notifier and (len(accounts) == 1 or telegram_notifier.mode == "all"):
                    telegram_notifier.notify_single_run(err_ctx, force=(len(accounts) == 1))

        # Check for any incoming telegram commands received during execution
        if telegram_notifier and telegram_notifier.is_configured:
            _process_pending_telegram_updates(telegram_notifier, account_mgr, client)

        # Send batch summary to Telegram
        if telegram_notifier:
            if results:
                telegram_notifier.notify_summary(results, force=force_notify)
            if telegram_notifier.is_stats_requested():
                logger.info("Stats breakdown requested; sending account breakdown to Telegram.")
                all_configured_accounts = account_mgr.list_accounts(enabled_only=False)
                results_map = {r.username.lower(): r for r in (results or [])}
                stats_items = []
                for a in all_configured_accounts:
                    ctx = results_map.get(a.username.lower())
                    if ctx and ctx.final_balance is not None:
                        stats_items.append(ctx)
                    else:
                        stats_items.append(a)
                telegram_notifier.send_stats_breakdown(stats_items or results or accounts)
                telegram_notifier.clear_stats_requested()
    except Exception as exc:
        logger.exception(f"Fatal error during rewards execution: {exc}")
        if telegram_notifier:
            telegram_notifier.send_raw_message(
                f"❌ <b>Thundr Fatal Error:</b> Произошла критическая ошибка: {exc}"
            )
        raise

    # Summary table
    table = Table(title="Execution Summary", show_header=True, header_style="bold magenta")
    table.add_column("Account", style="cyan")
    table.add_column("State", style="bold")
    table.add_column("Coins Earned", justify="right")
    table.add_column("Final Balance", justify="right")
    table.add_column("Streak", justify="right")
    table.add_column("Note")

    for r in results:
        state_color = {
            CrateState.CLAIMED: "green",
            CrateState.COOLDOWN: "yellow",
            CrateState.FAILED: "red",
            CrateState.UNCERTAIN: "magenta",
            CrateState.ERROR: "red",
            CrateState.NOT_AUTHENTICATED: "red",
        }.get(r.current_state, "white")

        table.add_row(
            r.username,
            f"[{state_color}]{r.current_state.value}[/{state_color}]",
            f"+{r.coins_earned}" if r.coins_earned > 0 else "0",
            str(r.final_balance or r.initial_balance or "N/A"),
            str(r.status.streak if r.status else 0),
            r.error_message or (r.state_history[-1][2] if r.state_history else ""),
        )

    console.print("\n", table)
    console.print("[bold green]✨ All accounts completed![/bold green]")


@cli.command("run")
@click.argument("account_arg", required=False, default=None)
@click.option("-a", "--account", "account_opt", default=None, help="Specific account username to process.")
@click.option("--all", "all_accounts", is_flag=True, default=False, help="Process all configured accounts.")
@click.option("--dry-run", is_flag=True, help="Simulate execution without claiming rewards.")
@click.option("--no-telegram", is_flag=True, help="Disable Telegram notifications for this run.")
@click.option("--force-notify", is_flag=True, help="Force sending Telegram summary even if throttled.")
def run_command(
    account_arg: Optional[str],
    account_opt: Optional[str],
    all_accounts: bool,
    dry_run: bool,
    no_telegram: bool,
    force_notify: bool,
) -> None:
    """Claim daily rewards for all configured accounts."""
    target = account_opt or account_arg
    _execute_rewards(
        account_name=target, dry_run=dry_run, no_telegram=no_telegram, force_notify=force_notify
    )


@cli.command("claim")
@click.argument("account_arg", required=False, default=None)
@click.option("-a", "--account", "account_opt", default=None, help="Specific account username to process.")
@click.option("--all", "all_accounts", is_flag=True, default=False, help="Process all configured accounts.")
@click.option("--dry-run", is_flag=True, help="Simulate execution without claiming rewards.")
@click.option("--no-telegram", is_flag=True, help="Disable Telegram notifications for this run.")
@click.option("--force-notify", is_flag=True, help="Force sending Telegram summary even if throttled.")
def claim_command(
    account_arg: Optional[str],
    account_opt: Optional[str],
    all_accounts: bool,
    dry_run: bool,
    no_telegram: bool,
    force_notify: bool,
) -> None:
    """Claim daily rewards for all configured accounts or a specific account (alias for 'run')."""
    target = account_opt or account_arg
    _execute_rewards(
        account_name=target, dry_run=dry_run, no_telegram=no_telegram, force_notify=force_notify
    )


@cli.command("check")
@click.argument("account_arg", required=False, default=None)
@click.option("-a", "--account", "account_opt", default=None, help="Specific account username to check.")
def check_command(account_arg: Optional[str], account_opt: Optional[str]) -> None:
    """Check coin balance and daily crate status without claiming."""
    account_name = account_opt or account_arg
    console.print("[bold cyan]🔍 Checking accounts status...[/bold cyan]")

    account_mgr = AccountManager()
    accounts = account_mgr.list_accounts(enabled_only=True)
    if account_name:
        key = account_name.strip().lower()
        accounts = [a for a in accounts if a.username.lower() == key]
        if not accounts:
            console.print(f"[bold red]❌ Account '{account_name}' not found.[/bold red]")
            return

    if not accounts:
        console.print("[yellow]⚠️ No accounts found.[/yellow]")
        return

    api_client = ThundrApiClient()
    browser_client = ThundrBrowserClient(
        headless=os.getenv("HEADLESS_BROWSER", "true").lower() == "true"
    )
    client = ThundrClient(api_client, browser_client)

    table = Table(title="Accounts Status", show_header=True, header_style="bold blue")
    table.add_column("Username", style="cyan")
    table.add_column("Available?", justify="center")
    table.add_column("Balance", justify="right")
    table.add_column("Streak", justify="right")
    table.add_column("Time Remaining", style="bold yellow")
    table.add_column("Last Reward", justify="right", style="green")
    table.add_column("Next Reset", style="dim")

    for acc in accounts:
        try:
            status = client.get_crate_status(acc)
            avail_str = "[bold green]YES[/bold green]" if status.available else "[yellow]COOLDOWN[/yellow]"
            server_now_dt = None
            if status.server_now:
                try:
                    server_now_dt = datetime.fromisoformat(status.server_now.replace("Z", "+00:00"))
                except Exception:
                    pass
            rem_str = TelegramNotifier.format_time_remaining(status.next_reset_at, now_dt=server_now_dt)
            last_rew = f"+{acc.last_reward_coins}" if acc.last_reward_coins else "N/A"
            table.add_row(
                acc.username,
                avail_str,
                str(status.coin_balance),
                str(status.streak),
                rem_str,
                last_rew,
                status.next_reset_at or "N/A",
            )
            # Update cache
            account_mgr.update_status(acc.username, status)
        except Exception as e:
            table.add_row(acc.username, "[red]ERROR[/red]", "N/A", "N/A", "N/A", "N/A", str(e)[:30])

    console.print(table)
    notifier = TelegramNotifier()
    cycle_info = notifier.determine_game_cycle(accounts)
    crate_rem = notifier.format_time_remaining(cycle_info["next_reset_iso"], now_dt=cycle_info["server_now_dt"])
    if crate_rem == "доступен сейчас! ⚡":
        crate_info_str = f"Crate ({cycle_info['cycle_display']}): [bold green]{crate_rem}[/bold green]"
    else:
        crate_info_str = f"Next Crate ({cycle_info['next_crate_display']}): {crate_rem}"

    console.print(
        f"\n[dim]Server Game Day: [bold]{cycle_info['cycle_display']}[/bold] | {crate_info_str}[/dim]"
    )


@cli.command("add-account")
@click.option("-u", "--username", prompt="Thundr Username", help="Thundr account username")
@click.option("-p", "--password", prompt="Thundr Password", hide_input=True, help="Thundr account password")
@click.option("--user-id", default="", help="Optional known user ID")
@click.option("--hashed-token", default="", help="Optional known hashed_token")
def add_account_command(username: str, password: str, user_id: str, hashed_token: str) -> None:
    """Add a new account to local accounts configuration."""
    account_mgr = AccountManager()
    rec = account_mgr.add_account(
        username=username,
        password=password,
        user_id=user_id or None,
        hashed_token=hashed_token or None,
    )
    console.print(f"[bold green]✅ Account '{rec.username}' added successfully![/bold green]")


@cli.command("remove-account")
@click.argument("username")
def remove_account_command(username: str) -> None:
    """Remove an account by username."""
    account_mgr = AccountManager()
    if account_mgr.remove_account(username):
        console.print(f"[bold green]✅ Account '{username}' removed.[/bold green]")
    else:
        console.print(f"[bold red]❌ Account '{username}' not found.[/bold red]")


@cli.command("list-accounts")
def list_accounts_command() -> None:
    """List all configured accounts (with secrets safely masked)."""
    account_mgr = AccountManager()
    accounts = account_mgr.list_accounts()

    if not accounts:
        console.print("[yellow]No accounts configured yet.[/yellow]")
        return

    table = Table(title="Configured Accounts", show_header=True, header_style="bold green")
    table.add_column("Username", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Has Password?", justify="center")
    table.add_column("Session Token", style="dim")
    table.add_column("Last Checked")

    for a in accounts:
        session_str = a.session.token_masked if a.session else "[dim]None[/dim]"
        pwd_str = "[green]Yes[/green]" if a.password else "[yellow]No[/yellow]"
        table.add_row(
            a.username,
            "[green]Enabled[/green]" if a.enabled else "[red]Disabled[/red]",
            pwd_str,
            session_str,
            a.last_checked_at or "Never",
        )

    console.print(table)


@cli.command("test")
@click.option("--with-browser", is_flag=True, help="Also test launching Playwright browser.")
@click.option("--with-telegram", is_flag=True, help="Send a test notification to Telegram.")
def test_command(with_browser: bool, with_telegram: bool) -> None:
    """Run diagnostics on API connectivity, browser setup, and Telegram."""
    console.print("[bold cyan]🩺 Running Diagnostics...[/bold cyan]\n")

    # 1. API Connectivity
    console.print("[bold]1. Testing Thundr API connectivity...[/bold]")
    api = ThundrApiClient()
    try:
        # Check base connectivity
        status, data, _ = api._request("GET", "https://thundr.com/api/geo")
        if status == 200:
            console.print("   [green]✔ Thundr Web API connection successful (HTTP 200)[/green]")
        else:
            console.print(f"   [yellow]⚠ Thundr Web returned status {status}[/yellow]")
    except Exception as e:
        console.print(f"   [red]✖ Failed to connect to Thundr Web: {e}[/red]")

    try:
        # Check ID endpoint
        status, data, _ = api._request("GET", "https://id.thundr.com/api/coins/balance/ping")
        console.print("   [green]✔ Thundr ID API reachable (gateway active)[/green]")
    except Exception as e:
        console.print(f"   [yellow]⚠ Note on Thundr ID endpoint: {e}[/yellow]")

    # 2. Browser Check
    if with_browser:
        console.print("\n[bold]2. Testing Playwright browser...[/bold]")
        try:
            import playwright
            browser = ThundrBrowserClient(headless=True, timeout_sec=15)
            console.print("   [green]✔ Playwright package is installed.[/green]")
        except Exception as e:
            console.print(f"   [red]✖ Playwright issue: {e}[/red]")
    else:
        console.print("\n[bold]2. Playwright browser check skipped (pass --with-browser to test).[/bold]")

    # 3. Telegram Check
    if with_telegram:
        console.print("\n[bold]3. Testing Telegram Bot notification...[/bold]")
        tg = TelegramNotifier()
        if tg.is_configured:
            ok = tg.send_raw_message(
                "⚡ <b>Thundr Daily Rewards</b>: Test notification ping successful!"
            )
            if ok:
                console.print("   [green]✔ Telegram test notification delivered successfully![/green]")
            else:
                console.print("   [red]✖ Telegram message delivery failed. Check bot token and chat ID.[/red]")
        else:
            console.print("   [yellow]⚠ Telegram bot token or chat ID not set in .env[/yellow]")
    else:
        console.print("\n[bold]3. Telegram check skipped (pass --with-telegram to test).[/bold]")

    console.print("\n[bold green]Diagnostics complete![/bold green]")


@cli.command("export")
@click.option("--format", "fmt", type=click.Choice(["csv", "json"], case_sensitive=False), default="csv")
@click.option("-o", "--output", help="Destination file path")
def export_command(fmt: str, output: Optional[str]) -> None:
    """Export claiming and coin history to CSV or JSON."""
    storage = StorageManager()
    if fmt == "csv":
        out_file = storage.export_csv(output)
        console.print(f"[bold green]✔ History exported to CSV: {out_file}[/bold green]")
    else:
        dest = Path(output or "data/history_export.json")
        dest.parent.mkdir(parents=True, exist_ok=True)
        import json
        with open(dest, "w", encoding="utf-8") as f:
            json.dump([e.to_dict() for e in storage.get_history(limit=1000)], f, indent=2)
        console.print(f"[bold green]✔ History exported to JSON: {dest}[/bold green]")


@cli.command("goal")
@click.option("--target", "-t", default=50, help="Target coins threshold per account (default: 50).")
@click.option("-a", "--account", "account_opt", default=None, help="Check goal for a specific account.")
@click.option("--no-telegram", is_flag=True, help="Do not send goal report to Telegram.")
def goal_command(target: int, account_opt: Optional[str], no_telegram: bool) -> None:
    """Track progress towards coin goal across accounts."""
    account_mgr = AccountManager()
    accounts = account_mgr.list_accounts(enabled_only=True)
    if account_opt:
        key = account_opt.strip().lower()
        accounts = [a for a in accounts if a.username.lower() == key]
        if not accounts:
            console.print(f"[bold red]❌ Account '{account_opt}' not found.[/bold red]")
            return

    if not accounts:
        console.print("[yellow]⚠️ No accounts configured.[/yellow]")
        return

    table = Table(title=f"🎯 Goal Progress (Target: {target} coins)", show_header=True, header_style="bold green")
    table.add_column("Account", style="cyan")
    table.add_column("Balance", justify="right")
    table.add_column("Progress", justify="right")
    table.add_column("Status", justify="center")

    reached_count = 0
    total_balance = 0
    for acc in accounts:
        bal = acc.balance or 0
        total_balance += bal
        pct = min(100.0, (bal / target * 100)) if target > 0 else 100.0
        reached = bal >= target
        if reached:
            reached_count += 1
            status_str = "[bold green]✔ Reached[/bold green]"
        else:
            needed = target - bal
            status_str = f"[yellow]-{needed} coins[/yellow]"
        table.add_row(acc.username, str(bal), f"{pct:.0f}%", status_str)

    console.print("\n", table)
    pct_total = (reached_count / len(accounts) * 100) if accounts else 0
    console.print(
        f"\n[bold]Goal Summary:[/bold] [green]{reached_count}/{len(accounts)}[/green] ({pct_total:.1f}%) "
        f"accounts have reached {target}+ coins. Total pool balance: [bold cyan]{total_balance}[/bold cyan] coins."
    )

    if not no_telegram:
        notifier = TelegramNotifier()
        if notifier.is_configured:
            _process_pending_telegram_updates(notifier, account_mgr)
            msg = notifier.format_summary_report(accounts)
            notifier.send_raw_message(msg)
            if notifier.is_stats_requested():
                all_accounts = account_mgr.list_accounts(enabled_only=False)
                notifier.send_stats_breakdown(all_accounts or accounts)
                notifier.clear_stats_requested()


@cli.command("summary")
@click.option("--no-telegram", is_flag=True, help="Do not send summary report to Telegram.")
def summary_command(no_telegram: bool) -> None:
    """Display and send executive summary report for all accounts."""
    account_mgr = AccountManager()
    accounts = account_mgr.list_accounts(enabled_only=True)
    if not accounts:
        console.print("[yellow]⚠️ No accounts configured.[/yellow]")
        return
    notifier = TelegramNotifier()
    if not no_telegram and notifier.is_configured:
        _process_pending_telegram_updates(notifier, account_mgr)

    summary_text = notifier.format_summary_report(accounts)
    console.print(summary_text)
    if not no_telegram and notifier.is_configured:
        notifier.send_raw_message(summary_text)
        console.print("[bold green]✔ Executive summary report sent to Telegram![/bold green]")
        if notifier.is_stats_requested():
            all_accounts = account_mgr.list_accounts(enabled_only=False)
            notifier.send_stats_breakdown(all_accounts or accounts)
            notifier.clear_stats_requested()
            console.print("[bold green]✔ Account stats breakdown sent to Telegram![/bold green]")


@cli.command("stats")
@click.option("--no-telegram", is_flag=True, help="Do not send stats report to Telegram.")
@click.option("--request-next", is_flag=True, help="Flag stats to be sent on next runner execution.")
def stats_command(no_telegram: bool, request_next: bool) -> None:
    """Display individual account coin balances and manage stats request flag."""
    notifier = TelegramNotifier()
    account_mgr = AccountManager()
    if request_next:
        notifier.set_stats_requested(True)
        console.print("[bold green]✔ Flag 'stats_requested' set to True! Next runner run will send breakdown.[/bold green]")
        return

    accounts = account_mgr.list_accounts(enabled_only=True)
    if not accounts:
        console.print("[yellow]⚠️ No accounts configured.[/yellow]")
        return

    breakdown_msgs = notifier.format_stats_breakdown(accounts)
    for msg in breakdown_msgs:
        console.print(msg)

    if not no_telegram and notifier.is_configured:
        _process_pending_telegram_updates(notifier, account_mgr)
        notifier.send_stats_breakdown(accounts)
        notifier.clear_stats_requested()
        console.print("[bold green]✔ Account stats breakdown sent to Telegram![/bold green]")


@cli.command("boost")
@click.option("-a", "--account", "account_opt", default=None, help="Specific account to spend boost on.")
@click.option("--all", "all_accounts", is_flag=True, default=False, help="Purchase boost for all eligible accounts.")
@click.option(
    "--type",
    "boost_type",
    type=click.Choice(["mini", "small", "normal", "super"], case_sensitive=False),
    default="mini",
    help="Boost type to purchase (mini: 100 coins, small: 450, normal: 1000, super: 2100).",
)
@click.option("--dry-run", is_flag=True, help="Simulate without spending coins.")
@click.option("--no-telegram", is_flag=True, help="Do not send report to Telegram.")
def boost_command(
    account_opt: Optional[str],
    all_accounts: bool,
    boost_type: str,
    dry_run: bool,
    no_telegram: bool,
) -> None:
    """Check or spend coins on Thundr boosts (mini, small, normal, super)."""
    cost_map = {"mini": 100, "small": 450, "normal": 1000, "super": 2100}
    cost = cost_map.get(boost_type.lower(), 100)
    console.print(
        f"[bold cyan]⚡ Thundr Boost Manager (Type: {boost_type.upper()}, Cost: {cost} coins)[/bold cyan]"
    )

    account_mgr = AccountManager()
    accounts = account_mgr.list_accounts(enabled_only=True)
    if account_opt:
        key = account_opt.strip().lower()
        accounts = [a for a in accounts if a.username.lower() == key]
        if not accounts:
            console.print(f"[bold red]❌ Account '{account_opt}' not found.[/bold red]")
            return

    if not accounts:
        console.print("[yellow]⚠️ No accounts found.[/yellow]")
        return

    if not no_telegram:
        notifier = TelegramNotifier()
        if notifier.is_configured:
            _process_pending_telegram_updates(notifier, account_mgr)
            msg = notifier.format_summary_report(accounts)
            notifier.send_raw_message(msg)
            if notifier.is_stats_requested():
                all_accounts = account_mgr.list_accounts(enabled_only=False)
                notifier.send_stats_breakdown(all_accounts or accounts)
                notifier.clear_stats_requested()

    eligible = [a for a in accounts if (a.balance or 0) >= cost]
    console.print(
        f"Found [bold green]{len(eligible)}/{len(accounts)}[/bold green] account(s) eligible for {boost_type} boost (balance >= {cost})."
    )

    if not account_opt and not all_accounts and not dry_run:
        console.print("[dim]Pass `-a <username>` or `--all` to purchase boost.[/dim]")
        return

    api_client = ThundrApiClient()
    browser_client = ThundrBrowserClient(
        headless=os.getenv("HEADLESS_BROWSER", "true").lower() == "true"
    )
    client = ThundrClient(api_client, browser_client)

    if all_accounts:
        if not eligible:
            console.print(f"[yellow]⚠️ No accounts eligible for {boost_type} boost (balance >= {cost}).[/yellow]")
            return
        console.print(f"[bold cyan]Processing {boost_type} boost for {len(eligible)} account(s)...[/bold cyan]")
        for acc in eligible:
            if dry_run:
                console.print(
                    f"[yellow]🧪 [DRY RUN] Would purchase {boost_type} boost for '{acc.username}' (balance: {acc.balance}).[/yellow]"
                )
            else:
                try:
                    res = client.spend_boost(acc, boost=boost_type)
                    if getattr(acc, "last_status", None) and hasattr(acc.last_status, "coin_balance"):
                        cur_bal = getattr(acc.last_status, "coin_balance", 0)
                        if isinstance(cur_bal, (int, float)):
                            acc.last_status.coin_balance = max(0, int(cur_bal) - cost)
                            account_mgr.save()
                    console.print(
                        f"[bold green]✔ Boost purchased for '{acc.username}'![/bold green] Response: {res}"
                    )
                except Exception as e:
                    console.print(f"[bold red]❌ Failed boost for '{acc.username}': {e}[/bold red]")
        return

    if account_opt:
        acc = accounts[0]
        if (acc.balance or 0) < cost:
            console.print(
                f"[red]❌ Account '{acc.username}' balance ({acc.balance}) is lower than boost cost ({cost}).[/red]"
            )
            return

        if dry_run:
            console.print(
                f"[yellow]🧪 [DRY RUN] Would purchase {boost_type} boost for '{acc.username}'.[/yellow]"
            )
            return

        console.print(f"[yellow]Purchasing {boost_type} boost for '{acc.username}'...[/yellow]")
        try:
            res = client.spend_boost(acc, boost=boost_type)
            if getattr(acc, "last_status", None) and hasattr(acc.last_status, "coin_balance"):
                cur_bal = getattr(acc.last_status, "coin_balance", 0)
                if isinstance(cur_bal, (int, float)):
                    acc.last_status.coin_balance = max(0, int(cur_bal) - cost)
                    account_mgr.save()
            console.print(
                f"[bold green]✔ Boost purchased successfully for '{acc.username}'![/bold green] Response: {res}"
            )
        except Exception as e:
            console.print(f"[bold red]❌ Failed to purchase boost: {e}[/bold red]")


def _run_supervisor(
    claim_interval_hours: int = 5,
    poll_interval_sec: int = 3,
    dry_run: bool = False,
    listen_only: bool = False,
) -> None:
    """Unified autonomous daemon & Telegram bot supervisor."""
    import time

    notifier = TelegramNotifier()
    account_mgr = AccountManager()
    api_client = ThundrApiClient()
    browser_client = ThundrBrowserClient(
        headless=os.getenv("HEADLESS_BROWSER", "true").lower() == "true"
    )
    client = ThundrClient(api_client, browser_client)

    mode_desc = "Listen-only" if listen_only else f"Claims every {claim_interval_hours}h + Live Telegram bot"
    console.print(f"[bold green]🤖 Starting Thundr Autonomous Supervisor ({mode_desc})...[/bold green]")
    if notifier.is_configured:
        console.print(f"[green]✔ Telegram bot active (Chat ID: {notifier.chat_id})[/green]")
    else:
        console.print("[yellow]⚠ Telegram bot not configured. Running scheduled claiming only.[/yellow]")

    last_claim_ts = 0.0 if not listen_only else time.time()
    offset = notifier.get_last_update_id()
    if offset is not None:
        offset += 1

    try:
        while True:
            now = time.time()

            # 1. Scheduled claim execution
            if not listen_only and (now - last_claim_ts >= claim_interval_hours * 3600):
                console.print(
                    "\n[bold cyan]⏰ Scheduled claim interval reached. Executing rewards claim...[/bold cyan]"
                )
                try:
                    _execute_rewards(dry_run=dry_run, force_notify=False)
                except Exception as exc:
                    logger.error(f"Error during scheduled supervisor claim: {exc}")
                last_claim_ts = time.time()

            # 2. Check Telegram updates if configured
            if notifier.is_configured:
                try:
                    updates = notifier.get_updates(offset=offset, timeout=poll_interval_sec)
                    for upd in updates:
                        upd_id = upd.get("update_id", 0)
                        offset = upd_id + 1
                        notifier.save_last_update_id(upd_id)

                        msg = upd.get("message") or {}
                        chat = msg.get("chat") or {}
                        if str(chat.get("id")) != str(notifier.chat_id):
                            continue

                        text = (msg.get("text") or "").strip()
                        if not text.startswith("/"):
                            continue

                        console.print(f"[yellow]📩 Telegram command received: {text}[/yellow]")
                        accounts = account_mgr.list_accounts(enabled_only=True)

                        parts = text.split()
                        base = parts[0].lower()
                        if base in ("/claim", "/run"):
                            target_user = parts[1] if len(parts) > 1 else None
                            if target_user:
                                notifier.send_raw_message(f"🚀 <i>Запускаю сбор наград для {target_user}...</i>")
                                _execute_rewards(account_name=target_user, dry_run=dry_run, force_notify=True)
                            else:
                                notifier.send_raw_message("🚀 <i>Запускаю сбор наград для всех аккаунтов...</i>")
                                _execute_rewards(dry_run=dry_run, force_notify=True)
                            last_claim_ts = time.time()
                        else:
                            reply = notifier.handle_command(text, accounts=accounts, client=client)
                            if reply:
                                notifier.send_raw_message(reply)
                except Exception as tg_err:
                    logger.debug(f"Error polling Telegram updates: {tg_err}")

            time.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Supervisor stopped by user.[/yellow]")


@cli.command("bot")
@click.option("--poll-interval", "-p", default=3, help="Polling interval in seconds (default: 3s).")
@click.option("--claim-interval", "-i", default=5, help="Claim interval in hours (default: 5).")
@click.option("--listen-only", is_flag=True, help="Only listen to commands without scheduled claiming.")
@click.option("--dry-run", is_flag=True, help="Simulate claims without spending/claiming.")
def bot_command(poll_interval: int, claim_interval: int, listen_only: bool, dry_run: bool) -> None:
    """Run interactive autonomous Telegram bot listening for /goal, /boost, /status, /claim."""
    _run_supervisor(
        claim_interval_hours=claim_interval,
        poll_interval_sec=poll_interval,
        dry_run=dry_run,
        listen_only=listen_only,
    )


@cli.command("daemon")
@click.option("--interval", "-i", default=5, help="Check interval in hours (default: 5).")
@click.option("--dry-run", is_flag=True, help="Simulate without claiming.")
def daemon_command(interval: int, dry_run: bool) -> None:
    """Run continuous autonomous daemon: claims every N hours AND responds to Telegram bot."""
    _run_supervisor(claim_interval_hours=interval, dry_run=dry_run, listen_only=False)


if __name__ == "__main__":
    cli()

