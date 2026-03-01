"""setup_ui.py — Interactive credential setup wizard and status commands.

Rich handles all display output (headers, colors, panels).
getpass handles all sensitive input (passwords, API keys) — it masks characters
and writes its prompt to stderr, keeping it on a separate stream from rich's
stdout output to avoid interleaving on Windows.

Prompting rules:
  - Email address  → plain input()  (not sensitive, masking would be confusing)
  - Password       → getpass()      (hidden input)
  - API key        → getpass()      (hidden input)
"""

import getpass
import sys

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule

from . import credentials_manager as cm

console = Console()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _mask(value: str) -> str:
    """Return a partially-masked string for display (e.g. 'sk-a...xyz1')."""
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}...{value[-4:]}"


def _prompt_visible(label: str, default: str = "") -> str:
    """Prompt for a non-sensitive value using plain input()."""
    display = f"  {label}"
    if default:
        display += f" [dim](Enter to keep: {_mask(default)})[/dim]"
    display += ": "
    console.print(display, end="")
    value = input().strip()
    return value or default


def _prompt_hidden(label: str, has_existing: bool = False) -> str:
    """
    Prompt for a sensitive value with hidden input via getpass.

    We flush both stdout and stderr before calling getpass to ensure
    rich has finished writing any pending output. On Windows, getpass
    uses msvcrt and writes its prompt to sys.stderr — a different stream
    from rich's stdout — so they do not interleave.
    """
    hint = " (press Enter to keep existing)" if has_existing else ""
    # Print the label via rich, then flush before handing off to getpass
    console.print(f"  {label}{hint}: ", end="")
    sys.stdout.flush()
    sys.stderr.flush()
    return getpass.getpass(prompt="", stream=sys.stderr)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def run_setup_wizard() -> bool:
    """
    Run the interactive credential setup wizard.

    Loads any existing stored credentials and pre-fills defaults so users
    can update a single field without re-entering everything.

    Returns True if all credentials were saved successfully, False otherwise.
    """
    console.print()
    console.print(
        Panel(
            "[bold cyan]Garmin Health Coach — Credential Setup[/bold cyan]\n"
            "[dim]Credentials are stored in your OS keychain — never written to disk "
            "as plain text.[/dim]",
            expand=False,
            border_style="cyan",
        )
    )
    console.print()

    existing = cm.load_all_credentials()
    new_credentials: dict[str, str] = {}

    # --- Section: Garmin Connect ---
    console.print(Rule("[bold]Garmin Connect[/bold]", style="dim"))
    console.print()

    email = _prompt_visible("Email", default=existing.get("garmin_email") or "")
    if not email:
        console.print("[red]  Email cannot be empty.[/red]")
        return False
    new_credentials["garmin_email"] = email

    has_pw = bool(existing.get("garmin_password"))
    # Ask twice when entering a new password to catch typos (easy to mistype
    # into a hidden field). Skip confirmation if the user keeps the existing value.
    while True:
        password = _prompt_hidden("Password", has_existing=has_pw)
        if not password:
            if has_pw:
                password = existing["garmin_password"]  # kept existing — no confirm needed
                break
            console.print("[yellow]  Password cannot be empty.[/yellow]")
            continue
        confirm = _prompt_hidden("Confirm Password")
        if password == confirm:
            break
        console.print("[red]  Passwords don't match — try again.[/red]")
    new_credentials["garmin_password"] = password

    # --- Section: Anthropic ---
    console.print()
    console.print(Rule("[bold]Anthropic[/bold]", style="dim"))
    console.print()

    has_key = bool(existing.get("anthropic_api_key"))
    api_key = _prompt_hidden("API Key (sk-ant-...)", has_existing=has_key)
    api_key = api_key or existing.get("anthropic_api_key") or ""
    if not api_key:
        console.print("[red]  API key cannot be empty.[/red]")
        return False
    new_credentials["anthropic_api_key"] = api_key

    # --- Save ---
    cm.save_all_credentials(new_credentials)

    console.print()
    console.print("[bold green]✓ Credentials saved to OS keychain.[/bold green]")
    console.print()
    return True


def show_status() -> None:
    """Print which credentials are currently stored, with masked values."""
    console.print()
    console.print(Rule("[bold]Stored Credentials[/bold]", style="dim"))
    console.print()

    any_missing = False
    for key in cm.CREDENTIAL_KEYS:
        label = cm.CREDENTIAL_LABELS[key]
        value = cm.load_credential(key)
        if value:
            console.print(
                f"  [green]✓[/green] {label}: [dim]{_mask(value)}[/dim]"
            )
        else:
            console.print(f"  [red]✗[/red] {label}: [dim]not set[/dim]")
            any_missing = True

    console.print()
    if any_missing:
        console.print(
            "[yellow]Run [bold]python main.py --setup[/bold] to add missing credentials.[/yellow]"
        )
    console.print()


def clear_credentials() -> None:
    """Prompt for confirmation, then delete all stored credentials."""
    console.print()
    console.print(
        "[yellow]This will remove all stored credentials from the OS keychain.[/yellow]"
    )
    console.print("  Confirm? [dim](y/N)[/dim]: ", end="")
    sys.stdout.flush()
    answer = input().strip().lower()

    if answer == "y":
        cm.delete_all_credentials()
        console.print("[green]✓ All credentials cleared.[/green]")
    else:
        console.print("[dim]Cancelled.[/dim]")
    console.print()
