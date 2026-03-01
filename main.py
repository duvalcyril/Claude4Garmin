"""main.py — Entry point for the Garmin Health Coach CLI.

Startup sequence:
  1. Parse CLI flags (--setup, --clear-credentials, --status)
  2. Load credentials from OS keychain (falls back to .env for existing users)
  3. Trigger setup wizard automatically if any credential is missing
  4. Authenticate with Garmin (reuse cached session token if available)
  5. Fetch and display 7 days of health data
  6. Start an interactive coaching chat loop with Claude

CLI usage:
  python main.py                    Normal run (auto-setup if credentials missing)
  python main.py --setup            Re-run the credential wizard (e.g., to rotate keys)
  python main.py --status           Show which credentials are stored in the keychain
  python main.py --clear-credentials  Remove all stored credentials
"""

import argparse
import os
import sys

from dotenv import load_dotenv
from garminconnect import GarminConnectAuthenticationError
from rich.console import Console

import credentials_manager as cm
from setup_ui import run_setup_wizard, show_status, clear_credentials
from garmin_client import get_garmin_client, fetch_health_data, format_health_summary
from claude_client import ClaudeCoach

console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Garmin Health Coach — AI-powered coaching from your wearable data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py              # normal run\n"
            "  python main.py --setup      # add or update credentials\n"
            "  python main.py --status     # check what's stored\n"
            "  python main.py --clear-credentials  # remove everything\n"
        ),
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Run the interactive credential setup wizard",
    )
    parser.add_argument(
        "--clear-credentials",
        action="store_true",
        help="Delete all stored credentials from the OS keychain",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show which credentials are currently stored",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    console.print()
    console.print("[bold cyan]╔══════════════════════════════╗[/bold cyan]")
    console.print("[bold cyan]║    Garmin Health Coach       ║[/bold cyan]")
    console.print("[bold cyan]╚══════════════════════════════╝[/bold cyan]")

    # --- Handle single-action flags first (no Garmin/Claude connection needed) ---

    if args.clear_credentials:
        clear_credentials()
        return

    if args.status:
        show_status()
        return

    if args.setup:
        success = run_setup_wizard()
        if not success:
            console.print("[red]Setup incomplete. Run again to retry.[/red]")
            sys.exit(1)
        console.print("[dim]Run [bold]python main.py[/bold] to start coaching.[/dim]\n")
        return

    # --- Load credentials (keyring → .env fallback → setup wizard) ---

    # Inject keychain credentials into os.environ so the Anthropic SDK
    # (which reads ANTHROPIC_API_KEY directly from os.environ) picks them up.
    cm.inject_into_env()

    # .env fallback: fills any gaps for users who set up credentials the old way.
    # load_dotenv() does NOT overwrite already-set env vars by default.
    load_dotenv()

    if not cm.credentials_complete():
        console.print(
            "\n[yellow]No credentials found. Starting setup wizard...[/yellow]"
        )
        success = run_setup_wizard()
        if not success:
            console.print("[red]Setup incomplete. Exiting.[/red]")
            sys.exit(1)
        # Re-inject now that the wizard has saved fresh credentials
        cm.inject_into_env()

    email = os.environ["GARMIN_EMAIL"]
    password = os.environ["GARMIN_PASSWORD"]

    # --- Connect to Garmin ---
    console.print("\n[dim]Connecting to Garmin Connect...[/dim]")
    garmin = None
    for _attempt in range(2):  # allow one inline credential retry
        try:
            garmin = get_garmin_client(email, password)
            break
        except GarminConnectAuthenticationError as e:
            # 401 = wrong email or password; surface that clearly instead of the raw URL
            if "401" in str(e):
                console.print(
                    "\n[red]✗ Incorrect email or password (Garmin returned 401).[/red]"
                )
            else:
                console.print(f"\n[red]✗ Garmin login failed: {e}[/red]")

            if _attempt == 0:
                console.print("  Would you like to update your credentials now? ", end="")
                sys.stdout.flush()
                answer = input().strip().lower()
                if answer == "y":
                    if not run_setup_wizard():
                        sys.exit(1)
                    cm.inject_into_env()
                    email = os.environ["GARMIN_EMAIL"]
                    password = os.environ["GARMIN_PASSWORD"]
                else:
                    console.print(
                        "[dim]Run [bold]python main.py --setup[/bold] to update credentials.[/dim]"
                    )
                    sys.exit(1)
            else:
                sys.exit(1)
        except Exception as e:
            console.print(f"\n[red]Failed to connect to Garmin: {e}[/red]")
            sys.exit(1)

    # --- Fetch health data ---
    console.print("[dim]Fetching your health data (last 7 days)...[/dim]\n")
    try:
        health_data = fetch_health_data(garmin)
    except Exception as e:
        console.print(f"[red]Failed to fetch health data: {e}[/red]")
        sys.exit(1)

    # --- Display the summary so the user sees what Claude will have context on ---
    summary = format_health_summary(health_data)
    console.print(summary)
    console.print("\n" + "─" * 52)

    # --- Start coaching session ---
    coach = ClaudeCoach(health_summary=summary)
    console.print("\n[bold]Your health coach is ready![/bold]")
    console.print("[dim]Commands: 'reset' = clear chat history | 'quit' = exit[/dim]\n")

    # --- Chat loop ---
    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n\n[dim]Stay consistent — see you next time![/dim]")
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit", "q"):
            console.print("\n[dim]Stay consistent — see you next time![/dim]")
            break

        if user_input.lower() == "reset":
            coach.reset_history()
            continue

        try:
            response = coach.chat(user_input)
            console.print(f"\n[bold green]Coach:[/bold green] {response}\n")
        except KeyboardInterrupt:
            console.print("\n\n[dim]Stay consistent — see you next time![/dim]")
            break
        except Exception as e:
            # Keep the loop alive on API errors — user can try again
            console.print(f"\n[red]Error: {e}[/red]\n")


if __name__ == "__main__":
    main()
