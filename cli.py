#!/usr/bin/env python3
"""
Command Line Interface (CLI) for Thundr Daily Rewards Automation.
Provides interactive account management, automated reward claiming, diagnostics, and export.
"""

from __future__ import annotations

import logging
import os
import sys
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


# Load environment variables from .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

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
from thundr.state_machine import CrateState

# Rich console
console = Console()


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


def _execute_rewards(account_name: Optional[str], dry_run: bool, no_telegram: bool) -> None:
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

    results = []
    for acc in accounts:
        console.print(f"\n[bold yellow]▶ Processing account: {acc.username}[/bold yellow]")
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
            )

        # Record in history
        storage_mgr.record_run(ctx)

        # Send individual Telegram alert if configured
        if telegram_notifier:
            telegram_notifier.notify_single_run(ctx)

    # Send batch summary to Telegram
    if telegram_notifier and len(results) > 1:
        telegram_notifier.notify_summary(results)

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
@click.option("-a", "--account", "account_name", help="Specific account username to process.")
@click.option("--all", "all_accounts", is_flag=True, default=False, help="Process all configured accounts.")
@click.option("--dry-run", is_flag=True, help="Simulate execution without claiming rewards.")
@click.option("--no-telegram", is_flag=True, help="Disable Telegram notifications for this run.")
def run_command(account_name: Optional[str], all_accounts: bool, dry_run: bool, no_telegram: bool) -> None:
    """Claim daily rewards for all configured accounts."""
    _execute_rewards(account_name=account_name, dry_run=dry_run, no_telegram=no_telegram)


@cli.command("claim")
@click.option("-a", "--account", "account_name", help="Specific account username to process.")
@click.option("--all", "all_accounts", is_flag=True, default=False, help="Process all configured accounts.")
@click.option("--dry-run", is_flag=True, help="Simulate execution without claiming rewards.")
@click.option("--no-telegram", is_flag=True, help="Disable Telegram notifications for this run.")
def claim_command(account_name: Optional[str], all_accounts: bool, dry_run: bool, no_telegram: bool) -> None:
    """Claim daily rewards for all configured accounts or a specific account (alias for 'run')."""
    _execute_rewards(account_name=account_name, dry_run=dry_run, no_telegram=no_telegram)


@cli.command("check")
@click.option("-a", "--account", "account_name", help="Specific account username to check.")
def check_command(account_name: Optional[str]) -> None:
    """Check coin balance and daily crate status without claiming."""
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
    table.add_column("Next Reset", style="dim")

    for acc in accounts:
        try:
            status = client.get_crate_status(acc)
            avail_str = "[bold green]YES[/bold green]" if status.available else "[yellow]COOLDOWN[/yellow]"
            table.add_row(
                acc.username,
                avail_str,
                str(status.coin_balance),
                str(status.streak),
                status.next_reset_at or "N/A",
            )
            # Update cache
            account_mgr.update_status(acc.username, status)
        except Exception as e:
            table.add_row(acc.username, "[red]ERROR[/red]", "N/A", "N/A", str(e)[:30])

    console.print(table)


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


if __name__ == "__main__":
    cli()
